"""封装与 CARLA 交互的客户端逻辑，负责加载地图、生成场景并采集传感器数据。"""

import copy
import random
import secrets
import weakref
from collections import defaultdict
from queue import Queue

import carla
import numpy as np

from graph.graph_manager import GraphManager
from manager import (
    AgentModelManager,
    CyclistManager,
    PedestrianManager,
    VehicleManager,
    WorldManager,
)
from misc.constant import BEV_CAMERA, FRONT_CAMERA
from scene_utils.retreival import retrieve_roads


def inverse_format_town_name(town_name):
    """将 CARLA 内部的 `Carla/Maps/TownXX` 名字转换为 GraphManager 需要的小写格式。"""
    if "/" in town_name:
        town_name = town_name.rsplit("/", 1)[-1]
    return town_name[:4].lower() + str(int(town_name[4:]))


def format_town_name(town_name):
    """反向格式化镇名，生成可供 CARLA 加载的 `TownXX` 字符串。"""
    town_num = town_name[4:]
    return f"Town{town_num.zfill(2)}"


class CarlaClient:
    """对 CARLA 服务端进行高层封装，负责生成场景并同步自定义管理器。"""
    def __init__(
        self,
        input_folder="maps",
        host="127.0.0.1",
        port=2000,
        use_cache=True,
        cache_dir="graph_cache",
        override_option=False,
    ):
        self.client = carla.Client(host, port)
        self.client.set_timeout(10.0)
        self.graph_manager = GraphManager(
            input_folder,
            use_cache=use_cache,
            cache_dir=cache_dir,
        )
        self.world = None
        self.agent_model_manager = AgentModelManager(self.client)
        self.vehicle_manager = VehicleManager(
            self.graph_manager, override_option=override_option
        )
        self.pedestrian_manager = PedestrianManager()
        self.cyclist_manager = CyclistManager(self.graph_manager)
        self.world_manager = WorldManager()

        self.front_camera = None
        self.front_image_queue = Queue()
        self.bev_camera = None
        self.bev_image_queue = Queue()

        self.json_file = None

        # Check the left, right, and straight lanes
        self.graph_manager.get_intersection_info(self, self.vehicle_manager)

    def set_json_file(self, json_file):
        """设置外部描述文件，格式包含分析、检索与规划三个部分。"""
        self.json_file = json_file

    def draw_point(self, location, color=(255, 0, 0)):
        """在世界中绘制调试点，方便验证检索结果或路径。"""
        self.world.debug.draw_string(
            location,
            "O",
            color=carla.Color(*color),
            life_time=10,
            draw_shadow=False,
            persistent_lines=True,
        )

    def load_map(self, map_name, weather="ClearNoon"):
        """按需加载地图和天气，并重新初始化各类管理器。"""
        if self.world is not None:
            self.clean()
            self.set_sync_mode(False, set_tm=False)
        self.world = self.client.load_world(map_name)
        if hasattr(carla.WeatherParameters, weather):
            self.world.set_weather(getattr(carla.WeatherParameters, weather))
        self.set_sync_mode(True, set_tm=False)
        self.set_manager()
        self.world.tick()
        self.set_traffic_light_time(5)

    def set_world(self, world):
        """直接使用已有 world 对象时，同样需要重置管理器引用。"""
        self.world = world
        self.set_manager()

    def set_manager(self):
        """将 CARLA 世界、蓝图库等对象注入各个 Manager，保持状态一致。"""
        self.agent_model_manager.set_blueprint(self.world.get_blueprint_library())
        self.world_manager.set_world(self.world)
        self.vehicle_manager.set_new_manager(
            self.agent_model_manager, self.world_manager
        )
        self.pedestrian_manager.set_new_manager(
            self.agent_model_manager, self.world_manager
        )
        self.cyclist_manager.set_new_manager(
            self.agent_model_manager, self.world_manager
        )
        self.graph_manager.set_town_name(
            inverse_format_town_name(self.world.get_map().name)
        )

    def set_traffic_light_time(self, duration=20):
        """统一设定交通灯红灯时长，便于复现实验条件。"""
        actor_list = self.world.get_actors()
        for actor_ in actor_list:
            if isinstance(actor_, carla.TrafficLight):
                actor_.set_red_time(duration)

    def set_sync_mode(self, sync, set_tm=False):
        """切换 CARLA 同步/异步模式，可选择同时控制 Traffic Manager。"""
        settings = self.world.get_settings()
        settings.synchronous_mode = sync
        if sync:
            settings.fixed_delta_seconds = 0.1
        else:
            settings.fixed_delta_seconds = None
        self.world.apply_settings(settings)
        if set_tm:
            self.tm.set_synchronous_mode(sync)

    @staticmethod
    def parse_image(weak_self, carla_image, name_to_put):
        """回调函数：将 CARLA 图像转换为 numpy，并入队等待消费。"""
        self = weak_self()
        np_img = np.frombuffer(carla_image.raw_data, dtype=np.dtype("uint8"))
        np_img = copy.deepcopy(np_img)
        np_img = np.reshape(np_img, (carla_image.height, carla_image.width, 4))
        np_img = np_img[:, :, :3][:, :, ::-1]
        getattr(self, name_to_put).put((carla_image.frame, np_img))

    def clean(self):
        """销毁当前场景中的所有 actor 与传感器，避免影响下次生成。"""
        self.vehicle_manager.clean()
        self.pedestrian_manager.clean()
        self.cyclist_manager.clean()
        if self.front_camera is not None:
            self.front_camera.stop()
            self.front_camera.destroy()
        if self.bev_camera is not None:
            self.bev_camera.stop()
            self.bev_camera.destroy()

        if self.front_camera is not None or self.bev_camera is not None:
            self.world.tick()

    def sort_road_target(
        self, road_info, required_num_of_waypoints, action_list, road_type_list
    ):
        """为可选道路打分，优先满足动作和道路类型的组合偏好。"""
        score = 0

        for action, relative_position in action_list:
            if (
                relative_position == "road_of_straight"
                and road_info["have_straight_from"]
            ):
                if action == "go_straight" and road_info["have_opposite"]:
                    score += 1
                elif action == "turn_left" and road_info["can_turn_right"]:
                    score += 1
                elif action == "turn_right" and road_info["can_turn_left"]:
                    score += 1
            elif (
                relative_position == "road_of_left_turn" and road_info["have_left_from"]
            ):
                if action == "go_straight" and road_info["can_turn_right"]:
                    score += 1
                elif action == "turn_left" and road_info["can_go_straight"]:
                    score += 1
                elif action == "turn_right" and road_info["have_opposite"]:
                    score += 1
            elif (
                relative_position == "road_of_right_turn"
                and road_info["have_right_from"]
            ):
                if action == "go_straight" and road_info["can_turn_left"]:
                    score += 1
                elif action == "turn_left" and road_info["have_opposite"]:
                    score += 1
                elif action == "turn_right" and road_info["can_go_straight"]:
                    score += 1
            elif not relative_position.startswith("road"):
                if action == "go_straight" and road_info["can_go_straight"]:
                    score += 1
                elif action == "turn_left" and road_info["can_turn_left"]:
                    score += 1
                elif action == "turn_right" and road_info["can_turn_right"]:
                    score += 1
        for road_type in road_type_list:
            if road_type == "sidewalk" and road_info["have_sidewalk"]:
                score += 1
            elif road_type == "shoulder" and road_info["have_shoulder"]:
                score += 1
        if road_info["num_of_waypoints"] < required_num_of_waypoints:
            score = 0
        return score

    def get_valid_road(
        self, road_condition, agent_type_list, action_list, road_type_list
    ):
        road = retrieve_roads(self.graph_manager.graph, road_condition)
        if len(road) == 0:
            return None, None, None, None

        required_num_of_waypoints = self.vehicle_manager.compute_approximate_num_points(
            agent_type_list, max(road_condition["number_of_lanes"], 1)
        )

        road_target = []
        check_set = defaultdict(set)
        for node_id, direction, node_info in road:
            town_name, road_id, _ = self.graph_manager.node_id_to_town_road_id(node_id)
            if road_id in check_set[town_name]:
                continue
            check_set[town_name].add(road_id)
            road_target.append(
                (format_town_name(town_name), [road_id], direction, node_info)
            )
        score_list = sorted(
            [
                (
                    self.sort_road_target(
                        x[3], required_num_of_waypoints, action_list, road_type_list
                    ),
                    idx,
                )
                for idx, x in enumerate(road_target)
            ],
            reverse=True,
        )
        max_score = score_list[0][0]

        length_having_same_score = sum([score == max_score for score, _ in score_list])
        road_target = [
            road_target[road_idx]
            for _, road_idx in score_list[:length_having_same_score]
        ]

        diff_number_of_lane = [
            4 - abs(road_info["number_of_lane"] - road_condition["number_of_lanes"])
            for *_, road_info in road_target
        ]
        total = sum(diff_number_of_lane)
        diff_number_of_lane = [diff / total for diff in diff_number_of_lane]
        select_target = np.random.choice(
            range(len(diff_number_of_lane)), 1, p=diff_number_of_lane
        ).squeeze()
        town_name, road_id, direction, road_info = road_target[select_target]
        return town_name, road_id, direction, road_info

    def spawn_ego_monitor(self):
        """为 ego 车辆绑定前视与鸟瞰相机，收集调试/评估所需图像。"""
        # Create sensor
        weak_self = weakref.ref(self)
        camera_rgb_bp = self.agent_model_manager.get_blueprint_from_name(
            "sensor.camera.rgb"
        )
        camera_rgb_bp.set_attribute("image_size_x", str(FRONT_CAMERA["image_size_x"]))
        camera_rgb_bp.set_attribute("image_size_y", str(FRONT_CAMERA["image_size_y"]))
        camera_rgb_bp.set_attribute("fov", str(FRONT_CAMERA["fov"]))
        front_transform = carla.Transform(
            carla.Location(
                x=FRONT_CAMERA["x"],
                y=FRONT_CAMERA["y"],
                z=FRONT_CAMERA["z"],
            ),
            carla.Rotation(
                roll=FRONT_CAMERA["roll"],
                pitch=FRONT_CAMERA["pitch"],
                yaw=FRONT_CAMERA["yaw"],
            ),
        )
        self.front_camera = self.world.spawn_actor(
            camera_rgb_bp, front_transform, attach_to=self.vehicle_manager.vehicles[0]
        )
        self.front_camera.listen(
            lambda image: self.parse_image(weak_self, image, "front_image_queue")
        )
        camera_rgb_bp.set_attribute("image_size_x", str(BEV_CAMERA["image_size_x"]))
        camera_rgb_bp.set_attribute("image_size_y", str(BEV_CAMERA["image_size_y"]))
        camera_rgb_bp.set_attribute("fov", str(BEV_CAMERA["fov"]))
        bev_transform = carla.Transform(
            carla.Location(
                x=BEV_CAMERA["x"],
                y=BEV_CAMERA["y"],
                z=BEV_CAMERA["z"],
            ),
            carla.Rotation(
                roll=BEV_CAMERA["roll"],
                pitch=BEV_CAMERA["pitch"],
                yaw=BEV_CAMERA["yaw"],
            ),
        )
        self.bev_camera = self.world.spawn_actor(
            camera_rgb_bp, bev_transform, attach_to=self.vehicle_manager.vehicles[0]
        )
        self.bev_camera.listen(
            lambda image: self.parse_image(weak_self, image, "bev_image_queue")
        )
        self.world.tick()

    def set_seed(self, seed=None):
        """统一设置 numpy 与 random 的随机种子，确保结果可复现。"""
        if seed is None:
            seed = secrets.randbelow(1_000_000_000)
        np.random.seed(seed)
        random.seed(seed)

    def spawn_all_agent(
        self,
        road_id,
        agent_info,
        direction,
        at_junction=False,
        ego_agents=None,
        ego_destination=None,
    ):
        """根据配置批量生成车辆/行人/骑行者，并返回 ego 的初始 waypoint。"""
        if ego_agents is None:
            ego_waypoint = self.vehicle_manager.spawn_ego_car(
                road_id, agent_info, direction, at_junction
            )
        else:
            ego_waypoint = self.world_manager.get_waypoint_from_location(
                ego_agents.get_location(), carla.LaneType.Driving
            )
            self.vehicle_manager.add_pre_spawn_ego(
                ego_agents,
                ego_waypoint,
                agent_info,
                destination=ego_destination,
            )

        pedestrian_agents = []
        cyclist_agents = []
        for agent in agent_info:
            if agent["type"] == "pedestrian":
                pedestrian_agents.append(agent)
            elif agent["type"] == "cyclist":
                cyclist_agents.append(agent)

        if ego_waypoint is not None:
            self.spawn_ego_monitor()
            self.vehicle_manager.spawn_other_cars(
                agent_info, self.vehicle_manager.vehicles[0]
            )

            self.pedestrian_manager.set_pos_id_to_waypoints(self.vehicle_manager)
            self.pedestrian_manager.spawn_pedestrians(
                pedestrian_agents, ego_agent=self.vehicle_manager.vehicle_agent[0]
            )

            self.cyclist_manager.set_pos_id_to_waypoints(self.vehicle_manager)
            self.cyclist_manager.spawn_cyclists(
                cyclist_agents, ego_agent=self.vehicle_manager.vehicle_agent[0]
            )
        return ego_waypoint

    def check_finish(self, timeout=10.0, tick_world=True):
        """驱动一次世界更新并获取传感器帧，返回车辆是否完成任务。"""
        vehicle_done = self.vehicle_manager.run_step()
        self.cyclist_manager.run_step()
        self.pedestrian_manager.run_step()
        if tick_world:
            self.world.tick()
        if not self.front_image_queue.empty():
            _, front_image = self.front_image_queue.get(True, timeout)
        else:
            front_image = None
        if not self.bev_image_queue.empty():
            _, bev_image = self.bev_image_queue.get(True, timeout)
        else:
            bev_image = None
        return vehicle_done, {"front_image": front_image, "bev_image": bev_image}

    def destroy(self, set_sync=True):
        """销毁当前会话并按需恢复异步模式，避免阻塞下次仿真。"""
        self.clean()
        if set_sync:
            self.set_sync_mode(False)

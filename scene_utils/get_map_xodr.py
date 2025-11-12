"""导出 CARLA 地图的 OpenDRIVE 数据，便于离线分析或复现。"""

import argparse

import carla


def parse_args():
    """解析命令行参数，允许用户自定义连接信息及输出路径。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--town", type=str, default="Town01")
    parser.add_argument("--output", type=str, default=None)
    return parser.parse_args()


def get_map_xodr(world, save_path=None):
    """从当前世界中提取 OpenDRIVE 内容并按需写入文件。"""
    opendrive = world.get_map().to_opendrive()
    if save_path:
        with open(save_path, "w") as file:
            file.write(opendrive)
    return opendrive


if __name__ == "__main__":
    args = parse_args()

    client = carla.Client(args.host, args.port)
    client.set_timeout(2.0)
    print(client.get_available_maps())
    world = client.load_world(args.town)
    get_map_xodr(world, args.output)

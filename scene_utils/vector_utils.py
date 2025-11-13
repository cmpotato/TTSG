"""向量计算相关的简单工具，用于路网方向判断。"""


def make_vector(waypoint1, waypoint2):
    """将两个 waypoint 的位置转换成二维向量。"""
    return (
        waypoint2.transform.location.x - waypoint1.transform.location.x,
        waypoint2.transform.location.y - waypoint1.transform.location.y,
    )


def vector_is_close(v1, v2, threshold=0.00001):
    """比较两个向量是否接近，常用于去重或稳定方向判断。"""
    return abs(v1[0] - v2[0]) < threshold and abs(v1[1] - v2[1]) < threshold


def cross_product(v1, v2):
    """二维向量叉乘，符号可以反映左右关系。"""
    return v1[0] * v2[1] - v1[1] * v2[0]


def is_counter_clockwise(v1, v2):
    """依据 CARLA 坐标系判断 `v2` 是否位于 `v1` 的逆时针方向。"""
    return cross_product(v1, v2) < 0

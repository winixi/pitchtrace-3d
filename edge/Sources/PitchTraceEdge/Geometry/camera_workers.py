"""按固定机位分配视觉工作进程，在同一曝光组内并发并按输入顺序收集结果。"""

from concurrent.futures import ThreadPoolExecutor, wait


def validate_worker_count(value):
    if type(value) is not int or not 1 <= value <= 8:
        raise ValueError("detector.workers 必须为 1–8 的整数")
    return value


class CameraWorkers:
    def __init__(self, camera_ids, workers):
        cameras = sorted(set(camera_ids))
        self.worker_count = min(validate_worker_count(workers), len(cameras))
        self._lanes = {camera: index % self.worker_count for index, camera in enumerate(cameras)}
        self._pools = []

    def __enter__(self):
        # 单进程沿用调用线程；同一机位的解码器和工作进程始终由同一线程驱动。
        if self.worker_count > 1:
            self._pools = [ThreadPoolExecutor(max_workers=1) for _ in range(self.worker_count)]
        return self

    def __exit__(self, *_):
        for pool in self._pools:
            pool.shutdown(wait=True, cancel_futures=True)
        self._pools = []

    def lane(self, camera_id):
        return self._lanes[camera_id]

    def map_group(self, rows, operation, *args):
        if not self._pools:
            return [operation(self.lane(row["cameraId"]), row, *args) for row in rows]
        futures = []
        for row in rows:
            lane = self.lane(row["cameraId"])
            futures.append(self._pools[lane].submit(operation, lane, row, *args))
        # 每次只派发一个曝光组。单路失败也等待其余已开始的工作结束后才退出该组。
        wait(futures)
        return [future.result() for future in futures]

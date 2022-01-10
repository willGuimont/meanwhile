import argparse
import json
import pathlib
import queue
import select
import subprocess
import sys
import threading
import time
import typing
from dataclasses import dataclass
from enum import Enum

from pynput import keyboard, mouse


@dataclass
class Message:
    msg_type: Enum
    data: typing.Optional[typing.Any] = None


class ListenerMessageType(Enum):
    STOP = 0


class UserEvent(Enum):
    KEY_PRESS = 1
    KEY_RELEASE = 2
    MOUSE_MOVE = 3
    MOUSE_CLICK = 4
    MOUSE_SCROLL = 5


class JobMessageType(Enum):
    STOP = 0
    USER_INTERACTION = 1


def keyboard_listener_thread(listener_queue: queue.Queue, job_queue: queue.Queue):
    mutex = threading.Lock()
    key_buffer = []

    def on_press(key):
        with mutex:
            key_buffer.append((UserEvent.KEY_PRESS, key))

    def on_release(key):
        with mutex:
            key_buffer.append((UserEvent.KEY_RELEASE, key))

    key_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    key_listener.start()
    while True:
        with mutex:
            if len(key_buffer):
                job_queue.put(Message(JobMessageType.USER_INTERACTION, ()))
                key_buffer.clear()
        try:
            msg: Message = listener_queue.get(block=False)
            if msg.msg_type == ListenerMessageType.STOP:
                job_queue.put(Message(JobMessageType.STOP))
                break
            else:
                raise ValueError(f'{msg.msg_type} is not a valid message type')
        except queue.Empty:
            ...
        time.sleep(0.5)
    key_listener.stop()


def mouse_listener_thread(listener_queue: queue.Queue, job_queue: queue.Queue):
    mutex = threading.Lock()
    mouse_buffer = []

    def on_move(x, y):
        with mutex:
            mouse_buffer.append((UserEvent.MOUSE_MOVE, (x, y)))

    def on_click(x, y, button, pressed):
        with mutex:
            mouse_buffer.append((UserEvent.MOUSE_CLICK, (x, y, button, pressed)))

    def on_scroll(x, y, dx, dy):
        with mutex:
            mouse_buffer.append((UserEvent.MOUSE_SCROLL, (x, y, dx, dy)))

    mouse_listener = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
    mouse_listener.start()
    while True:
        with mutex:
            if len(mouse_buffer) > 0:
                job_queue.put(Message(JobMessageType.USER_INTERACTION, ()))
                mouse_buffer.clear()
        try:
            msg: Message = listener_queue.get(block=False)
            if msg.msg_type == ListenerMessageType.STOP:
                job_queue.put(Message(JobMessageType.STOP))
                break
            else:
                raise ValueError(f'{msg.msg_type} is not a valid message type')
        except queue.Empty:
            ...
        time.sleep(0.5)
    mouse_listener.stop()


class TimeoutExpired(Exception):
    pass


def input_with_timeout(prompt: str, timeout: int) -> typing.Optional[str]:
    print(prompt)
    i, _, _ = select.select([sys.stdin], [], [], timeout)
    if i:
        return i[0].read(1)
    return None


def ask_kill(timeout=5) -> bool:
    answer = input_with_timeout('kill job? [y/n]', timeout)
    return answer is not None and answer.strip().lower() == 'y'


def get_current_job(state_path: pathlib.Path, job_names: [str]):
    if not state_path.exists():
        with open(state_path, 'w') as f:
            json.dump(dict(finished=[]), f)
    with open(state_path, 'r') as f:
        state = json.load(f)
        finished = state['finished']
        for i, j in enumerate(job_names):
            if j not in finished:
                return i
    return None


def finishing_job(state_path, name):
    with open(state_path, 'r') as f:
        state = json.load(f)
    state['finished'].append(name)
    with open(state_path, 'w') as f:
        json.dump(state, f)


def job_tread(jobs_path: pathlib.Path, delay: int, workdir: pathlib.Path, job_queue: queue.Queue,
              listener_queues: [queue.Queue]):
    state_path = workdir.joinpath('state.json')

    jobs = json.load(open(jobs_path, 'r'))
    job_names = [j['name'] for j in jobs['jobs']]

    p: typing.Optional[subprocess.Popen] = None
    last_time_since_event = time.time()

    while True:
        try:
            msg: Message = job_queue.get(block=False)
            if msg.msg_type == JobMessageType.USER_INTERACTION:
                if p is not None:
                    with job_queue.mutex:
                        job_queue.queue.clear()
                    if ask_kill():
                        print('Killing job...')
                        p.kill()
                        p = None
                        print('Done')
                    last_time_since_event = time.time()
            elif msg.msg_type == JobMessageType.STOP:
                if p is not None:
                    p.kill()
                break
            else:
                raise ValueError(f'{msg.msg_type} is not a valid message type')
        except queue.Empty:
            if p is None and (time.time() - last_time_since_event) / 60 > delay:
                current_job_index = get_current_job(state_path, job_names)
                if current_job_index is None:
                    for q in listener_queues:
                        q.put(Message(ListenerMessageType.STOP))
                    break
                current_job = jobs['jobs'][current_job_index]
                print(f'Starting {current_job["name"]}')
                log_file = open(workdir.joinpath(f'{current_job["name"]}.txt'), 'w')
                job_call = [current_job['command']]
                p = subprocess.Popen(job_call, shell=True, stdout=log_file)
            elif p is not None:
                if p.poll() is not None:
                    current_job_index = get_current_job(state_path, job_names)
                    finishing_job(state_path, job_names[current_job_index])
                    p = None
        time.sleep(1)


def main(jobs_path: str, delay: int, workdir: str):
    jobs_path = pathlib.Path(jobs_path)

    if not jobs_path.exists():
        print('job description file not found')
        exit(1)

    workdir = pathlib.Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    keyboard_listener_queue = queue.Queue()
    mouse_listener_queue = queue.Queue()
    job_queue = queue.Queue()

    kl_thread = threading.Thread(target=keyboard_listener_thread, args=(keyboard_listener_queue, job_queue))
    ml_thread = threading.Thread(target=mouse_listener_thread, args=(mouse_listener_queue, job_queue))
    j_thread = threading.Thread(target=job_tread, args=(
    jobs_path, delay, workdir, job_queue, [keyboard_listener_queue, mouse_listener_queue]))

    kl_thread.start()
    ml_thread.start()
    j_thread.start()

    try:
        kl_thread.join()
        ml_thread.join()
        j_thread.join()
    except KeyboardInterrupt:
        keyboard_listener_queue.put(Message(ListenerMessageType.STOP))
        mouse_listener_queue.put(Message(ListenerMessageType.STOP))
        job_queue.put(Message(JobMessageType.STOP))

    kl_thread.join()
    ml_thread.join()
    j_thread.join()

    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='meanwhile, run jobs when you\'re not using your computer')
    parser.add_argument('--jobs', '-j', required=True, help='json file describing jobs to run meanwhile')
    parser.add_argument('--delay', '-d', type=float, default=10.0,
                        help='delay, in minutes, after the last user input before starting a job')
    parser.add_argument('--workdir', '-wd', default='workdir', help='working directory for meanwhile')
    args = parser.parse_args()

    jobs = args.jobs
    delay = args.delay
    workdir = args.workdir

    ret = main(jobs, delay, workdir)

    exit(ret)

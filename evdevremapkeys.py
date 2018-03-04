#!/usr/bin/env python3
#
# Copyright (c) 2017 Philip Langdale
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


import argparse
import asyncio
from concurrent.futures import CancelledError
import functools
from pathlib import Path
import signal


import daemon
import evdev
from evdev import InputDevice, UInput, categorize, ecodes
from xdg import BaseDirectory
import yaml

DEFAULT_RATE = .1  # seconds
repeat_tasks = {}

@asyncio.coroutine
def handle_events(input, output, remappings):
    while True:
        events = yield from input.async_read()  # noqa
        for event in events:
            if event.type == ecodes.EV_KEY and \
               event.code in remappings:
                remap_event(output, event, remappings)
            else:
                output.write_event(event)
                output.syn()


@asyncio.coroutine
def repeat_event(event, rate, count, values, output):
    if count==0: count=-1
    while count is not 0:
        count-=1
        for value in values:
            event.value = value
            print('repeat event {} {}'.format(count, event))
            output.write_event(event)
            output.syn()
        yield from asyncio.sleep(rate)


def remap_event(output, event, remappings):
    for remapping in remappings[event.code]:
        pressed = event.value is 1
        ignore_depress = remapping.get('ignore_depress', False)
        if ignore_depress and not pressed:
            return
        original_code = event.code
        event.code = remapping['code']
        event.type = remapping.get('type', None) or event.type
        values = remapping.get('value', None) or [event.value]
        repeat = remapping.get('repeat', False)
        if repeat:
            rate = remapping.get('rate', DEFAULT_RATE)
            count = remapping.get('count', 0)
            repeat_task = repeat_tasks.pop(original_code, None)
            if repeat_task:
                repeat_task.cancel()
            if pressed:
                repeat_tasks[original_code] = asyncio.ensure_future(
                    repeat_event(event, rate, count, values, output))
        else:
            for value in values:
                event.value = value
                output.write_event(event)
                output.syn()

import pprint
def load_config(config_override):
    conf_path = None
    if config_override is None:
        for dir in BaseDirectory.load_config_paths('evdevremapkeys'):
            conf_path = Path(dir) / 'config.yaml'
            if conf_path.is_file():
                break
        if conf_path is None:
            raise NameError('No config.yaml found')
    else:
        conf_path = Path(config_override)
        if not conf_path.is_file():
            raise NameError('Cannot open %s' % config_override)

    with open(conf_path.as_posix(), 'r') as fd:
        config = yaml.safe_load(fd)
        for device in config['devices']:
            device['remappings'] = normalize_config(device['remappings'])
            device['remappings'] = resolve_ecodes(device['remappings'])
    pprint.PrettyPrinter().pprint(config)
    return config

# Converts general config schema
# {'remappings': {
#     'BTN_EXTRA': [
#         'KEY_Z',
#         'KEY_A',
#         {'code': 'KEY_X', 'value': 1}
#         {'code': 'KEY_Y', 'value': [1,0]]}
#     ]
# }}
# into fixed format
# {'remappings': {
#     'BTN_EXTRA': [
#         {'code': 'KEY_Z'},
#         {'code': 'KEY_A'},
#         {'code': 'KEY_X', 'value': [1]}
#         {'code': 'KEY_Y', 'value': [1,0]]}
#     ]
# }}
def normalize_config(remappings):
    norm = {}
    for key, mappings in remappings.items():
        new_mappings = []
        for mapping in mappings:
            if type(mapping) is str:
                new_mappings.append({'code': mapping})
            else:
                normalize_value(mapping)
                new_mappings.append(mapping)
        norm[key] = new_mappings
    return norm

def normalize_value(mapping):
    value = mapping.get('value')
    if value is None or type(value) is list:
        return
    mapping['value'] = [mapping['value']]

def resolve_ecodes(by_name):
    def resolve_mapping(mapping):
        if 'code' in mapping: mapping['code'] =  ecodes.ecodes[mapping['code']]
        if 'type' in mapping: mapping['type'] =  ecodes.ecodes[mapping['type']]
        return mapping
    return {ecodes.ecodes[key]: list(map(resolve_mapping, mappings))
        for key, mappings in by_name.items()}

def find_input(device):
    name = device.get('input_name', None);
    phys = device.get('input_phys', None);
    fn = device.get('input_fn', None);

    if name is None and phys is None and fn is None:
        raise NameError('Devices must be identified by at least one of "input_name", "input_phys", or "input_fn"');

    devices = [InputDevice(fn) for fn in evdev.list_devices()];
    for input in devices:
        if name != None and input.name != name:
            continue
        if phys != None and input.phys != phys:
            continue
        if fn != None and input.fn != fn:
            continue
        return input
    return None


def register_device(device):
    input = find_input(device)
    if input is None:
        raise NameError("Can't find input device '{}'".format(
            device.get('input_name', None) or device.get('input_phys', None)
            or device.get('input_fn', None)))
    input.grab()

    caps = input.capabilities()
    # EV_SYN is automatically added to uinput devices
    del caps[ecodes.EV_SYN]

    remappings = device['remappings']
    extended = set(caps[ecodes.EV_KEY])
    extended.update([l2['code'] for l1 in remappings.values() for l2 in l1])
    caps[ecodes.EV_KEY] = list(extended)

    output = UInput(caps, name=device['output_name'])

    asyncio.ensure_future(handle_events(input, output, remappings))


@asyncio.coroutine
def shutdown(loop):
    tasks = [task for task in asyncio.Task.all_tasks() if task is not
             asyncio.tasks.Task.current_task()]
    list(map(lambda task: task.cancel(), tasks))
    results = yield from asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()


def run_loop(args):
    config = load_config(args.config_file)
    for device in config['devices']:
        register_device(device)

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM,
                            functools.partial(asyncio.ensure_future,
                                              shutdown(loop)))

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        loop.remove_signal_handler(signal.SIGTERM)
        loop.run_until_complete(asyncio.ensure_future(shutdown(loop)))
    finally:
        loop.close()


def list_devices():
    devices = [InputDevice(fn) for fn in evdev.list_devices()];
    for device in reversed(devices):
        print('%s:\t"%s" | "%s"' % (device.fn, device.phys, device.name))

def test_device(dev_fn):
    device = evdev.InputDevice(dev_fn)
    for event in device.read_loop():
        if event.type == ecodes.EV_KEY:
            if event.code in ecodes.KEY:
                print('type:{}, code:{}, val:{}'.format(ecodes.EV[event.type], ecodes.KEY[event.code], event.value))
            if event.code in ecodes.BTN:
                print('type:{}, code:{}, val:{}'.format(ecodes.EV[event.type], ecodes.BTN[event.code], event.value))
        elif event.type == ecodes.EV_REL:
            print('type:{}, code:{}, val:{}'.format(ecodes.EV[event.type], ecodes.REL[event.code], event.value))
        elif event.type == ecodes.EV_MSC:
            pass
        elif event.type == ecodes.EV_SYN:
            pass
        else:
            print('type:{}, code:{}, val:{}'.format(ecodes.EV[event.type], event.code, event.value))

import time
if __name__ == '__main__':
    # mouse = InputDevice('/dev/input/event7')
    # ui = UInput.from_device(mouse, name='keyboard-mouse-device')
    # print(ui.capabilities(verbose=True).keys())
    # while True:
    #     ui.write(ecodes.EV_REL, ecodes.REL_WHEEL, 1)
    #     ui.syn()
    #     time.sleep(1)
    # ui.close()
    l = [1, 2, 3]
#     xxx = list(map(lambda x:x+1, l))
    xxx = [v+1 for v in l]


    parser = argparse.ArgumentParser(description='Re-bind keys for input devices')
    parser.add_argument('-d', '--daemon',
                        help='Run as a daemon', action='store_true')
    parser.add_argument('-f', '--config-file',
                        help='Config file that overrides default location')
    parser.add_argument('-l', '--list-devices', action='store_true',
                        help='List input devices by name and physical address')
    parser.add_argument('-t', '--test-device',
                        help='Test keyboard and mouse input keys')
    args = parser.parse_args()
    if args.list_devices:
        list_devices()
    elif args.test_device:
        test_device(args.test_device)
    elif args.daemon:
        with daemon.DaemonContext():
            run_loop(args)
    else:
        run_loop(args)

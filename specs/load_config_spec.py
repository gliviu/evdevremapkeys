#!/usr/bin/python3
import unittest
import os
import sys
from evdev import ecodes
import pprint

spec_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.append('{}/..'.format(spec_dir))
from evdevremapkeys import load_config

class TestLoadConfig(unittest.TestCase):
    def test_supports_simple_notation(self):
        mapping = remapping('config.yaml', ecodes.KEY_A)
        self.assertEqual("[{'code': 30}]", str(mapping))
    def test_supports_advanced_notation(self):
        mapping = remapping('config.yaml', ecodes.KEY_B)
        self.assertEqual("[{'code': 30}]", str(mapping))
    def test_resolves_single_value(self):
        mapping = remapping('config.yaml', ecodes.KEY_C)
        self.assertEqual("[{code: 30,value: [1]}]", pretty(mapping))
    def test_accepts_multiple_values(self):
        mapping = remapping('config.yaml', ecodes.KEY_D)
        self.assertEqual("[{code: 30,value: [1, 2]}]", pretty(mapping))
    def test_accepts_other_parameters(self):
        mapping = remapping('config.yaml', ecodes.KEY_E)
        self.assertEqual("[{code: 30,param1: p1,param2: p2}]", pretty(mapping))

def remapping(config_name, code):
    config_path = '{}/{}'.format(spec_dir, config_name)
    config = load_config(config_path)
    return config['devices'][0]['remappings'].get(code)

def pretty(mappings):
    def prettymapping(mapping):
        return '{'+','.join(['{}: {}'.format(key, mapping[key]) for key in sorted(mapping.keys())])+'}'
    return '['+','.join([prettymapping(mapping) for mapping in mappings])+']'

if __name__ == '__main__':
    unittest.main()

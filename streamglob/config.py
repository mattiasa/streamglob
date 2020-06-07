import logging
logger = logging.getLogger(__name__)
import os
import errno
import pytz
try:
    from collections.abc import Mapping, MutableMapping
except ImportError:
    from collections import Mapping, MutableMapping
import yaml
import functools
from orderedattrdict import Tree
import orderedattrdict.yamlutils
from orderedattrdict.yamlutils import AttrDictYAMLLoader
import distutils.spawn
import tzlocal

import getpass

PACKAGE_NAME="streamglob"

settings = None

def from_yaml_for_type(dict_type, loader, node):
    'Load mapping as AttrDict, preserving order'
    # Based on yaml.constructor.SafeConstructor.construct_mapping()
    d = dict_type()
    yield d
    if not isinstance(node, yaml.MappingNode):
        raise ConstructorError(
            None, None, 'expected a mapping node, but found %s' % node.id, node.start_mark)
    loader.flatten_mapping(node)
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=False)
        try:
            hash(key)
        except TypeError as exc:
            raise ConstructorError(
                'while constructing a mapping', node.start_mark,
                'found unacceptable key (%s)' % exc, key_node.start_mark)
        d[key] = loader.construct_object(value_node, deep=False)

def yaml_loader(node_type):

    from_yaml = functools.partial(from_yaml_for_type, node_type)

    cls_name = f"{node_type.__name__}YAMLLoader"

    def __init__(self, *args, **kwargs):
        super(cls, self).__init__(*args, **kwargs)
        def yaml_join(loader, node):
            seq = loader.construct_sequence(node)
            return ' '.join([str(i) for i in seq])
        self.add_constructor(u'tag:yaml.org,2002:map', from_yaml)
        self.add_constructor(u'tag:yaml.org,2002:omap', from_yaml)
        self.add_constructor('!join', yaml_join)

    d = {"__init__": __init__}

    cls = type(cls_name, (yaml.Loader,), d)
    return cls

class ConfigTree(Tree):

    def get_path(self, keys, default=None):
        return functools.reduce(
            lambda d, key: d.get(key, default)
            if isinstance(d, dict) else default,
            keys.split("."),
            self
        )


def dict_merge(dct, merge_dct):
    # via https://gist.github.com/angstwad/bf22d1822c38a92ec0a9
    dct = dct.copy()
    for k, v in merge_dct.items():
        if (k in dct and isinstance(dct[k], dict)
                and isinstance(merge_dct[k], Mapping)):
            dct[k] = dict_merge(dct[k], merge_dct[k])
        else:
            dct[k] = merge_dct[k]
    return dct

class ProfileTree(ConfigTree):

    DEFAULT_PROFILE_NAME = "default"

    def __init__(self, profile=DEFAULT_PROFILE_NAME, merge_default = False,
                 *args, **kwargs):
        self._merge_default = merge_default
        self._default_profile_name = profile
        self._profile_names = [self._default_profile_name]
        self.__exclude_keys__ |= {"profile_names", "foo", "_default_profile_name",
                                  "_merge_default", "profile"}
        self.include_profile(self._default_profile_name)
        super().__init__(*args, **kwargs)

    @property
    def profile(self):
        d = ConfigTree()
        for pn in self._profile_names:
            d = dict_merge(d, self[pn])
            # d = dict_merge(self[pn], d)

            # if (self._merge_default
            #     and self._profile_names != self._default_profile_names):
            #     return dict_merge(self[self._default_profile_names], p)
            # else:
            #     return p

        return d

    @property
    def profile_names(self):
        return self._profile_names

    def include_profile(self, profile):
        if profile == self._default_profile_name:
            return
        logger.debug(f"include_profile: {profile}")
        if not profile in self._profile_names:
            self._profile_names.append(profile)
        logger.debug(f"profiles: {self.profile_names}")

    def exclude_profile(self, profile):
        if profile == self._default_profile_name:
            return
        logger.debug(f"exclude_profile: {profile}")
        try:
            self.profile_names.remove(profile)
        except ValueError:
            pass
        logger.debug(f"profiles: {self.profile_names}")

    def toggle_profile(self, profile):
        if profile in self._profile_names:
            self.exclude_profile(profile)
        else:
            self.include_profile(profile)

    def reset_profiles(self):
        pass

    def __setattr__(self, name, value):
        if not name.startswith("_"):
            self[self._profile_name][name] = value
        else:
            object.__setattr__(self, name, value)

    def __getitem__(self, name):
        if isinstance(name, tuple):
            return functools.reduce(
                lambda a, b: ProfileTree(a, **{ k: v for k, v in b.items() if k not in a}),
                [ self[p] for p in reversed(name) ]
            )

        else:
            return super(ProfileTree, self).__getitem__(name)

class Config(ConfigTree):

    DEFAULT_PROFILE = "default"

    DEFAULT_CONFIG_DIR = os.path.expanduser(f"~/.config/{PACKAGE_NAME}")
    CONFIG_FILE_NAME = "config.yaml"

    def __init__(self, config_dir=None,
                 merge_default = False, *args, **kwargs):
        super(Config, self).__init__(*args, **kwargs)
        self.__exclude_keys__ |= {"_config_dir", "include_profile", "_profile_tree"}
        # self._config_file = config_file
        self._config_dir = config_dir or self.DEFAULT_CONFIG_DIR
        self.load()
        self._profile_tree = ProfileTree(
            **self.profiles,
            merge_default=merge_default
        )

    @property
    def config_file(self):
        return os.path.join(self.CONFIG_DIR, self.CONFIG_FILE_NAME)

    @property
    def CONFIG_DIR(self):
        return self._config_dir

    @property
    def LOG_FILE(self):
        return os.path.join(self.CONFIG_DIR, f"{PACKAGE_NAME}.log")

    @property
    def profile(self):
        return self._profile_tree.profile

    @property
    def profiles(self):
        return self._profile_tree

    @property
    def profile_names(self):
        return self._profile_tree.profile_names

    def include_profile(self, profile):
        self._profile_tree.include_profile(profile)

    def exclude_profile(self, profile):
        self._profile_tree.exclude_profile(profile)

    def toggle_profile(self, profile):
        self._profile_tree.toggle_profile(profile)

    def load(self):
        if not os.path.exists(self.config_file):
            raise Exception(f"config file {self.config_file} not found")
        config = yaml.load(open(self.config_file), Loader=yaml_loader(ConfigTree))
        self.update(config.items())

    def save(self):

        d = Tree([ (k, v) for k, v in self.items()])
        d.update({"profiles": self._profile_tree})
        with open(self._config_file, 'w') as outfile:
            yaml.dump(d, outfile, default_flow_style=False, indent=4)


def load(config_dir=None, merge_default=False):
    global settings
    settings = Config(config_dir, merge_default=merge_default)

# settings = Config(CONFIG_FILE, merge_default=True)

__all__ = [
    "CONFIG_DIR",
    "settings"
]

def main():
    test_settings = Config(
        os.path.expanduser("~/.config/streamglob.feeds"),
        merge_default=True
    )
    # print(test_settings)
    # print(list(test_settings.profile.providers.keys()))
    # test_settings.include_profile("proxy")
    test_settings.profile_names
    print(test_settings.profile.players)
    test_settings.include_profile("small")
    print(test_settings.profile_names)
    print(test_settings.profile.players)
    # print(test_settings.profiles["default"])
    # print(test_settings.profiles[("default")].get("env"))
    # print(test_settings.profiles[("default", "540p")].get("env"))
    # print(test_settings.profiles[("default", "540p")].get("env"))
    # print(test_settings.profiles[("default", "540p", "proxy")].get("env"))

if __name__ == "__main__":
    main()

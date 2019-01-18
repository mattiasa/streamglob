from __future__ import unicode_literals
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

from prompt_toolkit import prompt
from prompt_toolkit.validation import Validator, ValidationError
from prompt_toolkit.shortcuts import confirm
from prompt_toolkit.shortcuts import prompt
import getpass

PACKAGE_NAME="streamglob"
CONFIG_DIR=os.path.expanduser(f"~/.config/{PACKAGE_NAME}")
CONFIG_FILE=os.path.join(CONFIG_DIR, "config.yaml")
LOG_FILE=os.path.join(CONFIG_DIR, f"{PACKAGE_NAME}.log")

KNOWN_PLAYERS = ["mpv", "vlc"]

settings = None

class NotEmptyValidator(Validator):

    def validate(self, document):
        text = document.text
        if not len(text):
            raise ValidationError(message="Please supply a value")

class RangeNumberValidator(Validator):

    def __init__(self, minimum=None, maximum=None):
        self.minimum = minimum
        self.maximum = maximum

    def validate(self, document):

        text = document.text

        if not text:
            raise ValidationError(message="Please supply a value")

        if text.isdigit():
            value = int(text)
        else:
            i = 0

            raise ValidationError(
                message='Please enter an integer.'
            )

        if self.minimum and value < self.minimum:
            raise ValidationError(
                message="Value must be greater than %s" %(self.minimum)
            )

        if self.maximum and value > self.maximum:
            raise ValidationError(
                message="Value must be less than %s" %(self.maximum)
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

class ProfileTree(Tree):

    DEFAULT_PROFILE_NAME = "default"

    def __init__(self, profile=DEFAULT_PROFILE_NAME, merge_default = False,
                 *args, **kwargs):
        super(ProfileTree, self).__init__(*args, **kwargs)
        self._merge_default = merge_default
        self._default_profile_name = profile
        self.__exclude_keys__ |= {"_profile_name", "_default_profile_name",
                                  "_merge_default", "profile"}
        self.set_profile(self._default_profile_name)

    @property
    def profile(self):
        p = self[self._profile_name]
        if (self._merge_default
            and self._profile_name != self._default_profile_name):
            return dict_merge(self[self._default_profile_name], p)
        else:
            return p


    def set_profile(self, profile):
        self._profile_name = profile

    # def __getattr__(self, name):
    #     if not name.startswith("_"):
    #         p = self.profile
    #         val = p.get(name)
    #         if (self._merge_default
    #             and self._profile_name != self._default_profile_name):
    #             default_profile = self[self._default_profile_name]
    #             if isinstance(val, list):
    #                 val += default_profile.get(name, [])
    #             elif isinstance(val, dict):
    #                 val = default_profile.get(name, {})
    #                 val.update(**self.get(name, {}))
    #         return val
    #     return super().__getattr_(self, name)
    #     # raise AttributeError

    def __setattr__(self, name, value):
        if not name.startswith("_"):
            self[self._profile_name][name] = value
        else:
            object.__setattr__(self, name, value)

    def get(self, name, default=None):
        return self.profile.get(name, default)
        # p = self.profile
        # return (
        #     p.get(name, default)
        #     if name in p
        #     else self[self._default_profile_name].get(name, default)
        # )

    def __getitem__(self, name):
        if isinstance(name, tuple):
            return functools.reduce(
                lambda a, b: ProfileTree(a, **{ k: v for k, v in b.items() if k not in a}),
                [ self[p] for p in reversed(name) ]
            )

        else:
            return super(ProfileTree, self).__getitem__(name)

class Config(Tree):

    DEFAULT_PROFILE = "default"

    def __init__(self, config_file, merge_default = False, *args, **kwargs):
        super(Config, self).__init__(*args, **kwargs)
        self.__exclude_keys__ |= {"_config_file", "set_profile", "_profile_tree"}
        self._config_file = config_file
        self.load()
        self._profile_tree = ProfileTree(**self.profiles,
                                         merge_default=merge_default)


    def init_config(self):

        raise Exception("""
        Sorry, this configurator needs to be updated  to reflect recent changes
        to the config file.  Until this is fixed, use the sample config found
        in the "docs" directory of the distribution.
        """)

        from .session import StreamSession, SGStreamSessionException

        def mkdir_p(path):
            try:
                os.makedirs(path)
            except OSError as exc:  # Python >2.5
                if not (exc.errno == errno.EEXIST and os.path.isdir(path)):
                    raise

        def find_players():
            for p in KNOWN_PLAYERS:
                player = distutils.spawn.find_executable(p)
                if player:
                    yield player

        StreamSession.destroy()
        if os.path.exists(CONFIG_FILE):
            os.remove(CONFIG_FILE)

        time_zone = None
        player = None
        mkdir_p(CONFIG_DIR)

        while True:
            self.profile.username = prompt(
                "MLB.com username: ",
                validator=NotEmptyValidator())
            self.profile.password =  prompt(
                'Enter password: ',
                is_password=True, validator=NotEmptyValidator())
            try:
                s = StreamSession(self.profile.username,
                               self.profile.password)
                s.login()
                break
            except SGStreamSessionException:
                print("Couldn't login to MLB, please check your credentials.")
                continue

        tz_local = tzlocal.get_localzone().zone

        # password = prompt("MLB.tv password (will be stored in clear text!): ")
        found_players = list(find_players())
        if not found_players:
            print("no known media players found")
        else:
            print("found the following media players")
            print("\n".join(
                [ "\t%d: %s" %(n, p)
                  for n, p in enumerate(
                          ["My player is not listed"] + found_players
                  )]))
            choice = int(
                prompt(
                    "Select the number corresponding to your preferred player,\n"
                    "or 0 if your player is not listed: ",
                    validator=RangeNumberValidator(maximum=len(found_players))))
            if choice:
                player = found_players[choice-1]

        while not player:
            response = prompt("Please enter the path to your media player: ")
            player = distutils.spawn.find_executable(response)
            if not player:
                print("Couldn't locate player '%s'" %(response))

        player_args = prompt(
            "If you need to pass additional arguments to your media "
            "player, enter them here: ")
        if player_args:
            player = " ".join([player, player_args])

        self.profile.player = player

        print("\n".join(
            [ "\t%d: %s" %(n, l)
              for n, l in enumerate(
                      utils.MLB_HLS_RESOLUTION_MAP
              )]))
        print("Select a default video resolution for MLB.tv streams:")
        choice = int(
            prompt(
                "Choice: ",
                validator=RangeNumberValidator(maximum=len(utils.MLB_HLS_RESOLUTION_MAP))))
        if choice is not None:
            self.profile.default_resolution = utils.MLB_HLS_RESOLUTION_MAP[
                list(utils.MLB_HLS_RESOLUTION_MAP.keys())[choice]
            ]

        print("Your system time zone seems to be %s." %(tz_local))
        if not confirm("Is that the time zone you'd like to use? (y/n) "):
            while not time_zone:
                response = prompt("Enter your preferred time zone: ")
                if response in pytz.common_timezones:
                    time_zone = response
                    break
                elif confirm("Can't find time zone %s: are you sure? (y/n) "):
                    time_zone = response
                    break

        else:
            time_zone = tz_local

        self.profile.time_zone = time_zone
        self.save()

    @property
    def profile(self):
        return self._profile_tree.profile

    @property
    def profiles(self):
        return self._profile_tree

    def set_profile(self, profile):
        self._profile_tree.set_profile(profile)

    def load(self):
        if not os.path.exists(self._config_file):
            raise Exception(f"config file {self._config_file} not found")
        config = yaml.load(open(self._config_file), Loader=AttrDictYAMLLoader)
        self.update(config.items())

    def save(self):

        d = Tree([ (k, v) for k, v in self.items()])
        d.update({"profiles": self._profile_tree})
        with open(self._config_file, 'w') as outfile:
            yaml.dump(d, outfile, default_flow_style=False, indent=4)


def load(merge_default=False):
    global settings
    settings = Config(CONFIG_FILE, merge_default=merge_default)

# settings = Config(CONFIG_FILE, merge_default=True)

__all__ = [
    "CONFIG_DIR",
    "settings"
]

def main():
    test_settings = Config(
        os.path.expanduser("~/.config/streamglob/config.yaml"),
        merge_default=True
    )
    print(test_settings)
    # print(list(test_settings.profile.providers.keys()))
    test_settings.set_profile("proxy")
    print(list(test_settings.profile.proxies.keys()))
    raise Exception
    print(test_settings.profile.get("env"))
    print(test_settings.profiles["default"])
    print(test_settings.profiles[("default")].get("env"))
    print(test_settings.profiles[("default", "540p")].get("env"))
    print(test_settings.profiles[("default", "540p")].get("env"))
    print(test_settings.profiles[("default", "540p", "proxy")].get("env"))

if __name__ == "__main__":
    main()

import logging
logger = logging.getLogger(__name__)

import os
from datetime import datetime, timedelta

from pony.orm import *

from . import config
from . import providers

DB_FILE=os.path.join(config.CONFIG_DIR, "streamglob.sqlite")

CACHE_DURATION_SHORT = 60 # 60 seconds
CACHE_DURATION_MEDIUM = 60*60*24 # 1 day
CACHE_DURATION_LONG = 60*60*24*30  # 30 days
CACHE_DURATION_DEFAULT = CACHE_DURATION_SHORT

db = Database()

class CacheEntry(db.Entity):

    url = Required(str, unique=True)
    response = Required(bytes)
    last_seen = Required(datetime, default=datetime.now())

    @classmethod
    @db_session
    def purge(cls, age=CACHE_DURATION_LONG):

        cls.select(
            lambda e: e.last_seen < datetime.now() - timedelta(seconds=age)
        ).delete()


class Feed(db.Entity):

    DEFAULT_UPDATE_INTERVAL = 3600
    DEFAULT_ITEM_LIMIT = 100

    feed_id = PrimaryKey(int, auto=True)
    name = Optional(str, index=True)
    provider_name = Required(str, index=True)
    updated = Optional(datetime)
    update_interval = Required(int, default=DEFAULT_UPDATE_INTERVAL)
    items = Set(lambda: Item)

    @property
    def provider(self):
        return providers.get(self.provider_name)


class Item(db.Entity):

    item_id = PrimaryKey(int, auto=True)
    feed = Required(lambda: Feed)
    guid = Required(str, index=True)
    subject = Required(str)
    content = Required(Json)
    created = Required(datetime, default=datetime.now())
    seen = Optional(datetime)
    downloaded = Optional(datetime)
    # was_downloaded = Required(bool, default=False)


class ProviderData(db.Entity):
    # Providers inherit from this to define their own fields
    classtype = Discriminator(str)




def init(*args, **kwargs):

    db.bind("sqlite", create_db=True, filename=DB_FILE, *args, **kwargs)
    db.generate_mapping(create_tables=True)
    CacheEntry.purge()

def main():

    init()

if __name__ == "__main__":
    main()

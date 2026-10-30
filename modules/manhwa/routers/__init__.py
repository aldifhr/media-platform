from modules.manhwa.routers.core import register_core
from modules.manhwa.routers.content import register_content


def register_routers(app):
    register_core(app)
    register_content(app)

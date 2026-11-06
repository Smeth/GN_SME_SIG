"""Compatibility API for the shared layout configuration guard."""
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.services.shared_reference_write import shared_reference_write
from sme_gend.services.shared_config_write import decode_payload,load_shared_payload,save_shared_model
KEY="liste_present_layout"


def load_layout_payload(settings):
    return load_shared_payload(settings,KEY)


def save_layout(settings,cfg):
    return save_shared_model(settings,KEY,cfg)

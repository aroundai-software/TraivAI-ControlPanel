"""Vercel loads this file. The password and service role key come from the project settings."""

from access import open_config
from directory import SUPABASE_URL, SubscriptionDirectory
from server import create_app

_config = open_config()
app = create_app(_config, SubscriptionDirectory(SUPABASE_URL, lambda: _config.service_role_key))

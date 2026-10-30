"""platform-worker: notification engine + background jobs.

Runs the durable-outbox notification engine (claims platform.notification_outbox,
resolves guild subscriptions + routes, enqueues deliver_message onto arq). It
holds NO Discord bot token — delivery happens in the platform-discord process.
"""

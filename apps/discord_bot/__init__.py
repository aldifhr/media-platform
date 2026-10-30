"""platform-discord: the single Discord bot process.

The ONLY process that reads DISCORD_BOT_TOKEN. Hosts the HTTP-interactions
endpoint (inbound slash commands / buttons, Ed25519-verified) and, from
Phase 4, consumes arq ``deliver_*`` jobs for outbound delivery. Workers and
scrapers never see the token.
"""

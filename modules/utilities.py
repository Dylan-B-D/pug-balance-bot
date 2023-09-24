import discord

def get_user_id_by_name(guild, username):
    member = discord.utils.get(guild.members, name=username) or discord.utils.get(guild.members, display_name=username)
    if member:
        return member.id
    return None
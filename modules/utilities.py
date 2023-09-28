import discord
from data.user_roles import privileged_users, bot_admins

def get_user_id_by_name(guild, username):
    member = discord.utils.get(guild.members, name=username) or discord.utils.get(guild.members, display_name=username)
    if member:
        return member.id
    return None

async def check_bot_admin(ctx):
    """
    Checks if the context's author is a bot admin.
    Returns True if the author is a bot admin, else sends an error embed and returns False.
    """
    if ctx.author.id not in bot_admins:
        embed = discord.Embed(title="Permission Denied", description="Admin permissions required to execute this command.", color=0xff0000)
        await ctx.send(embed=embed)
        return False
    return True

import discord
from discord import app_commands
import yt_dlp
import asyncio
from src import log
from youtubesearchpython import VideosSearch

logger = log.setup_logger(__name__)

class MusicPlayer:
    def __init__(self):
        self.queue = []
        self.current_song = None
        self.ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }

    async def play_next(self, interaction):
        if not self.queue:
            await interaction.followup.send("Queue is empty. Disconnecting...")
            await interaction.guild.voice_client.disconnect()
            return

        self.current_song = self.queue.pop(0)
        interaction.guild.voice_client.play(
            discord.FFmpegPCMAudio(self.current_song['url']), 
            after=lambda e: asyncio.run_coroutine_threadsafe(self.play_next(interaction), interaction.client.loop)
        )
        await interaction.followup.send(f"Now playing: {self.current_song['title']}")

music_player = MusicPlayer()

async def play(interaction: discord.Interaction, query: str):
    if not interaction.user.voice:
        await interaction.followup.send("You need to be in a voice channel to use this command.")
        return

    channel = interaction.user.voice.channel
    if not interaction.guild.voice_client:
        await channel.connect()
    elif interaction.guild.voice_client.channel != channel:
        await interaction.guild.voice_client.move_to(channel)

    try:
        # Check if the query is a URL
        if not query.startswith('http://') and not query.startswith('https://'):
            # If not a URL, search for the video
            search = VideosSearch(query, limit=1)
            search_result = search.result()
            if not search_result['result']:
                await interaction.followup.send("No results found for the given query.")
                return
            url = search_result['result'][0]['link']
        else:
            url = query

        with yt_dlp.YoutubeDL(music_player.ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info:
                # This is a playlist or a list of videos
                info = info['entries'][0]
            url2 = info['url']
            title = info['title']

        music_player.queue.append({'url': url2, 'title': title})
        await interaction.followup.send(f"Added to queue: {title}")

        if not interaction.guild.voice_client.is_playing():
            await music_player.play_next(interaction)
    except Exception as e:
        logger.error(f"Error in play command: {str(e)}")
        await interaction.followup.send(f"An error occurred while trying to play the video: {str(e)}")

async def stop(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        music_player.queue.clear()
        music_player.current_song = None
        await interaction.followup.send("Stopped playback and cleared the queue.")
    else:
        await interaction.followup.send("I'm not currently in a voice channel.")

async def pause(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        interaction.guild.voice_client.pause()
        await interaction.followup.send("Playback paused.")
    else:
        await interaction.followup.send("Nothing is playing right now.")

async def resume(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_paused():
        interaction.guild.voice_client.resume()
        await interaction.followup.send("Playback resumed.")
    else:
        await interaction.followup.send("Playback is not paused.")

async def next(interaction: discord.Interaction):
    if interaction.guild.voice_client and (interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused()):
        interaction.guild.voice_client.stop()
        await interaction.followup.send("Skipping to the next song.")
    else:
        await interaction.followup.send("Nothing is playing right now.")
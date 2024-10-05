import discord
from discord import app_commands
import yt_dlp
import asyncio
import os
import threading
from src import log
from youtubesearchpython import VideosSearch

logger = log.setup_logger(__name__)

class MusicPlayer:
    def __init__(self):
        self.queue = []
        self.current_song = None
        self.is_playing = False
        self.voice_client = None
        self.ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': 'temp_audio.%(ext)s',
        }
        self.ffmpeg_options = {
            'options': '-vn'
        }

    async def play_next(self, interaction):
        if not self.queue:
            await interaction.followup.send("Queue is empty. Disconnecting...")
            if self.voice_client:
                await self.voice_client.disconnect()
            self.is_playing = False
            self.voice_client = None
            return

        self.current_song = self.queue.pop(0)
        self.is_playing = True

        if not self.voice_client or not self.voice_client.is_connected():
            self.voice_client = await interaction.user.voice.channel.connect()

        try:
            # Download audio
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                ydl.download([self.current_song['url']])

            # Play audio
            audio_source = discord.FFmpegPCMAudio('temp_audio.mp3', **self.ffmpeg_options)
            self.voice_client.play(audio_source, after=lambda e: asyncio.run_coroutine_threadsafe(self.song_finished(interaction), interaction.client.loop))

            await interaction.followup.send(f"Now playing: {self.current_song['title']}")
            logger.info(f"Started playing: {self.current_song['title']}")

        except Exception as e:
            logger.error(f"Error playing {self.current_song['title']}: {str(e)}")
            await interaction.followup.send(f"An error occurred while playing {self.current_song['title']}: {str(e)}")
            await self.song_finished(interaction)

    async def song_finished(self, interaction):
        self.is_playing = False
        # Clean up temporary files
        if os.path.exists('temp_audio.mp3'):
            os.remove('temp_audio.mp3')
        await self.play_next(interaction)

music_player = MusicPlayer()

async def play(interaction: discord.Interaction, query: str):
    if not interaction.user.voice:
        await interaction.followup.send("You need to be in a voice channel to use this command.")
        return

    channel = interaction.user.voice.channel
    if not music_player.voice_client:
        music_player.voice_client = await channel.connect()
    elif music_player.voice_client.channel != channel:
        await music_player.voice_client.move_to(channel)

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
            title = info['title']

        music_player.queue.append({'url': url, 'title': title})
        await interaction.followup.send(f"Added to queue: {title}")

        if not music_player.is_playing:
            await music_player.play_next(interaction)
    except Exception as e:
        logger.error(f"Error in play command: {str(e)}")
        await interaction.followup.send(f"An error occurred while trying to play the video: {str(e)}")

async def stop(interaction: discord.Interaction):
    if music_player.voice_client:
        music_player.is_playing = False
        music_player.voice_client.stop()
        await music_player.voice_client.disconnect()
        music_player.queue.clear()
        music_player.current_song = None
        music_player.voice_client = None
        await interaction.followup.send("Stopped playback and cleared the queue.")
    else:
        await interaction.followup.send("I'm not currently in a voice channel.")

async def pause(interaction: discord.Interaction):
    if music_player.voice_client and music_player.voice_client.is_playing():
        music_player.voice_client.pause()
        await interaction.followup.send("Playback paused.")
    else:
        await interaction.followup.send("Nothing is playing right now.")

async def resume(interaction: discord.Interaction):
    if music_player.voice_client and music_player.voice_client.is_paused():
        music_player.voice_client.resume()
        await interaction.followup.send("Playback resumed.")
    else:
        await interaction.followup.send("Playback is not paused.")

async def next(interaction: discord.Interaction):
    if music_player.voice_client and music_player.is_playing:
        music_player.voice_client.stop()
        await interaction.followup.send("Skipping to the next song.")
    else:
        await interaction.followup.send("No song is currently playing.")
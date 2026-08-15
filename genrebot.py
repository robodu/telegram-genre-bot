import telebot
import requests
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from spotipy.cache_handler import MemoryCacheHandler
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import html
import os
from google import genai

# --- CLOUD CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
LASTFM_API_KEY = os.getenv('LASTFM_API_KEY')
DISCOGS_TOKEN = os.getenv('DISCOGS_TOKEN')
SPOTIPY_CLIENT_ID = os.getenv('SPOTIPY_CLIENT_ID')
SPOTIPY_CLIENT_SECRET = os.getenv('SPOTIPY_CLIENT_SECRET')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# Initialize Telegram & Spotify
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
spotify = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIPY_CLIENT_ID,
    client_secret=SPOTIPY_CLIENT_SECRET,
    cache_handler=MemoryCacheHandler()
))

# Initialize Gemini Client
gemini_client = genai.Client(api_key=GEMINI_API_KEY)

def make_hashtags(genres_string):
    if not genres_string or genres_string == "Not found":
        return "Not found"
    tags = genres_string.split(", ")
    clean_tags = [f"#{t.replace(' ', '').replace('-', '').replace('/', '').replace('&', 'And').replace('\'', '')}" for t in tags]
    return " ".join(clean_tags)

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Send me a song and artist, and I'll build a complete profile across multiple databases!")

@bot.message_handler(func=lambda message: True)
def get_multi_genres(message):
    query = message.text
    bot.send_chat_action(message.chat.id, 'upload_photo')

    track_name = query
    artist_name = "Unknown"
    album_name = "Unknown"
    image_url = ""
    spotify_url = ""
    
    lastfm_genres = "Not found"
    deezer_genres = "Not found"
    mb_genres = "Not found"
    discogs_output = "Not found"

    # 1. SPOTIFY API
    try:
        if " - " in query:
            parts = query.split(" - ", 1)
            spotify_query = f"track:{parts[0].strip()} artist:{parts[1].strip()}"
        else:
            spotify_query = query

        results = spotify.search(q=spotify_query, type='track', limit=1)
        tracks = results['tracks']['items']
        if tracks:
            best = tracks[0]
            track_name = best['name']
            artist_name = best['artists'][0]['name']
            album_name = best['album']['name']
            spotify_url = best['external_urls']['spotify']
            images = best['album']['images']
            if images:
                image_url = images[0]['url']
    except Exception as e:
        print(f"Spotify Error: {e}")

    # 2. DEEZER API
    try:
        deezer_search = requests.get(f"https://api.deezer.com/search?q={query}").json()
        if deezer_search.get('data'):
            best = deezer_search['data'][0]
            if artist_name == "Unknown":
                track_name = best['title']
                artist_name = best['artist']['name']
                album_name = best['album']['title']
            
            album_id = best['album']['id']
            deezer_album = requests.get(f"https://api.deezer.com/album/{album_id}").json()
            if 'genres' in deezer_album and 'data' in deezer_album['genres']:
                d_genres = [g['name'] for g in deezer_album['genres']['data']]
                if d_genres:
                    deezer_genres = ", ".join(d_genres)
            if not image_url:
                image_url = best['album'].get('cover_xl', '')
    except Exception as e:
        print(f"Deezer Error: {e}")

    # 3. LAST.FM API
    try:
        search_query = f"{track_name} {artist_name}" if artist_name != "Unknown" else query
        search_params = {"method": "track.search", "track": search_query, "api_key": LASTFM_API_KEY, "format": "json"}
        search_response = requests.get("http://ws.audioscrobbler.com/2.0/", params=search_params).json()
        tracks = search_response.get('results', {}).get('trackmatches', {}).get('track', [])

        if tracks:
            best = tracks[0]
            info_params = {"method": "track.getInfo", "api_key": LASTFM_API_KEY, "artist": best['artist'], "track": best['name'], "format": "json"}
            info_response = requests.get("http://ws.audioscrobbler.com/2.0/", params=info_params).json()
            tags = info_response.get('track', {}).get('toptags', {}).get('tag', [])
            if tags:
                lastfm_genres = ", ".join([t['name'].title() for t in tags[:5]])
            images = info_response.get('track', {}).get('album', {}).get('image', [])
            if not image_url and images and images[-1].get('#text'):
                image_url = images[-1].get('#text')
    except Exception as e:
        print(f"Last.fm Error: {e}")

# 4. MUSICBRAINZ API
    try:
        # MusicBrainz requires a contact email in the header to prevent silent blocks
        headers = {'User-Agent': 'TelegramGenreBot/1.0 ( rdaighu@gmail.com )'}
        
        if artist_name != "Unknown":
            # Search the Artist instead of the Recording for a 100x better tag hit rate
            mb_query = f'"{artist_name}"'
            mb_search = requests.get(f"https://musicbrainz.org/ws/2/artist?query={mb_query}&fmt=json", headers=headers).json()
            
            if mb_search.get('artists'):
                best_mb = mb_search['artists'][0]
                if 'tags' in best_mb:
                    sorted_tags = sorted(best_mb['tags'], key=lambda x: x.get('count', 0), reverse=True)
                    m_genres = [t['name'].title() for t in sorted_tags[:5]]
                    if m_genres:
                        mb_genres = ", ".join(m_genres)
    except Exception as e:
        print(f"MusicBrainz Error: {e}")
        
    # 5. DISCOGS API
    try:
        discogs_headers = {'User-Agent': 'TelegramGenreBot/1.0', 'Authorization': f'Discogs token={DISCOGS_TOKEN}'}
        disc_query = f"{track_name} {artist_name}" if artist_name != "Unknown" else query
        discogs_search = requests.get(f"https://api.discogs.com/database/search?q={disc_query}", headers=discogs_headers).json()
        if discogs_search.get('results'):
            best = discogs_search['results'][0]
            combined = []
            if 'genre' in best: combined.extend(best['genre'])
            if 'style' in best: combined.extend(best['style'])
            if combined:
                seen = set()
                discogs_output = ", ".join([x for x in combined if not (x in seen or seen.add(x))])
            if not image_url and best.get('cover_image'):
                image_url = best['cover_image']
    except Exception as e:
        print(f"Discogs Error: {e}")

    # 6. GEMINI API (AI Vibe Check)
    gemini_summary = "Not available."
    try:
        if artist_name != "Unknown":
            prompt = f"In exactly one short, engaging sentence, describe the musical style, mood, and vibe of the song '{track_name}' by {artist_name}."
            response = gemini_client.models.generate_content(
                model='gemini-3.7-flash',
                contents=prompt
            )
            if response.text:
                gemini_summary = response.text.strip()
    except Exception as e:
        print(f"Gemini Error: {e}")

    # 7. HTML FORMATTING & SENDING
    response_text = f"🎵 <b>Song:</b> {html.escape(track_name)}\n"
    response_text += f"🎤 <b>Artist:</b> {html.escape(artist_name)}\n"
    response_text += f"💽 <b>Album:</b> {html.escape(album_name)}\n\n"
    
    response_text += f"✨ <b>Genre Check:</b> <i>{html.escape(gemini_summary)}</i>\n\n"
    
    response_text += "<blockquote>"
    response_text += f"💿 <b>Discogs:</b> {html.escape(discogs_output)}\n"
    response_text += f"🎸 <b>Last.&#8203;fm:</b> {html.escape(lastfm_genres)}\n"
    response_text += f"🧠 <b>MusicBrainz:</b> {html.escape(mb_genres)}\n"
    response_text += f"🎧 <b>Deezer:</b> {html.escape(deezer_genres)}"
    response_text += "</blockquote>"

    markup = InlineKeyboardMarkup()
    if spotify_url:
        markup.add(InlineKeyboardButton("🎧 Listen on Spotify", url=spotify_url))

    try:
        if image_url:
            bot.send_photo(message.chat.id, photo=image_url, caption=response_text, parse_mode="HTML", reply_markup=markup)
        else:
            bot.send_message(message.chat.id, response_text, parse_mode="HTML", reply_markup=markup)
    except Exception as e:
        bot.reply_to(message, f"Oops! Error: {e}")

print("Bot is polling on Railway with Gemini AI integration...")
bot.infinity_polling()

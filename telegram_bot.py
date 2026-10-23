import os
import shutil
import time
import socket
import asyncio
import subprocess
import yt_dlp
import imageio_ffmpeg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.request import HTTPXRequest
from telegram.error import TimedOut, NetworkError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# 1. إجبار الاتصال على استخدام IPv4 لمنع تعليق الشبكة أو الـ VPN
orig_getaddrinfo = socket.getaddrinfo
def getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
    return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = getaddrinfo_ipv4

# =====================================================================
# ضع التوكن الذي حصلت عليه من @BotFather بين علامتي التنصيص بالأسفل
BOT_TOKEN = "8796625262:AAFx5qoYOKITLoPkaEyQbqau7LWvUTm7bYM"
# =====================================================================

DOWNLOAD_DIR = os.path.join(os.getcwd(), "bot_downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def setup_ffmpeg():
    """تجهيز أداة FFmpeg بالاسم القياسي لضمان عمل الصوت 100%"""
    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    local_ffmpeg = os.path.join(os.getcwd(), exe_name)
    if not os.path.exists(local_ffmpeg):
        try:
            print("تجهيز أداة الصوت FFmpeg لأول مرة...")
            shutil.copyfile(imageio_ffmpeg.get_ffmpeg_exe(), local_ffmpeg)
        except Exception as e:
            print(f"تحذير أثناء تجهيز FFmpeg: {e}")
    return local_ffmpeg


FFMPEG_EXE = setup_ffmpeg()
FFMPEG_DIR = os.path.dirname(FFMPEG_EXE)


def get_video_info(url: str) -> dict:
    """جلب معلومات الفيديو من يوتيوب مع مهلة اتصال أطول"""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 60,
        "retries": 5,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def download_media_sync(url: str, mode: str, unique_id: str) -> tuple[str, str]:
    """تحميل الفيديو أو الصوت ودمجهما بصيغة متوافقة وسريعة الرفع"""
    base_path = os.path.join(DOWNLOAD_DIR, f"media_{unique_id}")

    if mode == "mp3":
        raw_audio_template = f"{base_path}_raw.%(ext)s"
        ydl_opts = {
            "format": "ba[ext=m4a]/ba/b",
            "outtmpl": raw_audio_template,
            "ffmpeg_location": FFMPEG_DIR,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 60,
            "retries": 10,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "Audio")
            downloaded_file = ydl.prepare_filename(info)

        mp3_path = f"{base_path}.mp3"
        # تحويل الصوت إلى MP3 بحجم خفيف (128k) ليرتفع بسرعة على تلجرام
        subprocess.run(
            [FFMPEG_EXE, "-y", "-i", downloaded_file, "-vn", "-ab", "128k", mp3_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        if os.path.exists(downloaded_file):
            os.remove(downloaded_file)
        return mp3_path, title

    else:
        # اختيار صيغ MP4 جاهزة أو سريعة الدمج بحجم مناسب لسرعة الرفع في تلجرام
        if mode == "high":
            fmt = (
                "bv*[height<=720][ext=mp4][filesize<40M]+ba[ext=m4a]/"
                "b[height<=720][ext=mp4]/"
                "bv*[height<=720]+ba/b"
            )
        else:  # جودة متوسطة وسريعة الرفع جداً (360p / 480p)
            fmt = (
                "b[height<=480][ext=mp4]/"
                "bv*[height<=360][ext=mp4]+ba[ext=m4a]/"
                "bv*[height<=360]+ba/b[height<=480]"
            )

        ydl_opts = {
            "format": fmt,
            "merge_output_format": "mp4",
            "ffmpeg_location": FFMPEG_DIR,
            # ضمان عمل الصوت (AAC) وتجهيز الفيديو للمشاهدة المباشرة (faststart)
            "postprocessor_args": {
                "ffmpeg": ["-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart"]
            },
            "outtmpl": f"{base_path}.%(ext)s",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 60,
            "retries": 10,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "Video")

        final_mp4 = f"{base_path}.mp4"
        return final_mp4, title


# ======================== أوامر ومعالجات البوت ========================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **أهلاً بك في بوت تحميل يوتيوب الاحترافي!**\n\n"
        "📌 أرسل أي **رابط فيديو من يوتيوب**، وسأخيرك بين:\n"
        "🎬 تحميل الفيديو بجودة عالية (720p HD)\n"
        "⚡ تحميل الفيديو بجودة سريعة وخفيفة (360p/480p)\n"
        "🎵 تحميل الصوت فقط بصيغة (MP3)"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not ("youtube.com" in url or "youtu.be" in url):
        await update.message.reply_text("⚠️ يرجى إرسال رابط يوتيوب صحيح.")
        return

    status_msg = await update.message.reply_text("🔍 جاري فحص الرابط وجلب بيانات الفيديو...")

    try:
        info = await asyncio.to_thread(get_video_info, url)
        title = info.get("title", "فيديو يوتيوب")
        duration = info.get("duration", 0)
        mins, secs = divmod(duration, 60) if duration else (0, 0)

        context.user_data["current_url"] = url

        keyboard = [
            [
                InlineKeyboardButton("🎬 جودة عالية (720p)", callback_data="dl|high"),
                InlineKeyboardButton("⚡ جودة سريعة وخفيفة", callback_data="dl|med"),
            ],
            [
                InlineKeyboardButton("🎵 صوت فقط (MP3)", callback_data="dl|mp3"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        info_text = (
            f"✅ **تم العثور على الفيديو:**\n\n"
            f"📌 **العنوان:** {title}\n"
            f"⏱ **المدة:** {int(mins):02d}:{int(secs):02d}\n\n"
            f"👇 **اختر الصيغة التي تريد تحميلها:**"
        )
        await status_msg.edit_text(info_text, reply_markup=reply_markup, parse_mode="Markdown")

    except Exception as e:
        await status_msg.edit_text(f"❌ تعذر جلب بيانات الفيديو. تأكد من الرابط أو اتصال الإنترنت.\n\nالتفاصيل: {e}")


async def upload_with_retries(bot, chat_id: int, file_path: str, mode: str, title: str, max_retries: int = 3):
    """دالة ذكية لرفع الملف إلى تلجرام مع إعادة المحاولة تلقائياً إذا ضعف الإنترنت"""
    for attempt in range(1, max_retries + 1):
        try:
            with open(file_path, "rb") as media_file:
                if mode == "mp3":
                    await bot.send_audio(
                        chat_id=chat_id,
                        audio=media_file,
                        title=title,
                        caption=f"🎵 {title}",
                        read_timeout=900,
                        write_timeout=900,
                        connect_timeout=120,
                    )
                else:
                    await bot.send_video(
                        chat_id=chat_id,
                        video=media_file,
                        caption=f"🎬 {title}",
                        supports_streaming=True,
                        read_timeout=900,
                        write_timeout=900,
                        connect_timeout=120,
                    )
            return  # نجح الرفع، نخرج من الدالة
        except (TimedOut, NetworkError) as e:
            if attempt == max_retries:
                raise e
            print(f"⚠️ تعثر الرفع في المحاولة {attempt}، جاري إعادة المحاولة خلال 3 ثوانٍ...")
            await asyncio.sleep(3)


async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    url = context.user_data.get("current_url")
    if not url:
        await query.edit_message_text("⚠️ انتهت صلاحية الجلسة، يرجى إرسال الرابط مرة أخرى.")
        return

    _, mode = query.data.split("|")
    unique_id = f"{query.from_user.id}_{int(time.time())}"
    file_path = None

    try:
        await query.edit_message_text(
            "⏳ **1/2 جاري تحميل الفيديو من يوتيوب ودمج الصوت...**",
            parse_mode="Markdown",
        )

        file_path, title = await asyncio.to_thread(download_media_sync, url, mode, unique_id)

        if not os.path.exists(file_path):
            raise FileNotFoundError("لم يتم العثور على الملف بعد التحميل.")

        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if file_size_mb > 49.5:
            await query.edit_message_text(
                f"⚠️ حجم الملف ({file_size_mb:.1f} MB) يتجاوز حد تلجرام المجاني (50 MB).\n"
                "💡 اضغط على الرابط مرة أخرى واختر **⚡ جودة سريعة وخفيفة** أو **🎵 صوت فقط**."
            )
            return

        await query.edit_message_text(
            f"📤 **2/2 اكتمل التحميل! جاري رفع الملف ({file_size_mb:.1f} MB) إلى تلجرام...**\n"
            "*(قد يستغرق الرفع دقيقة أو دقيقتين حسب سرعة الرفع لديك)*",
            parse_mode="Markdown",
        )

        # رفع الملف مع مهلة مفتوحة (15 دقيقة) وإعادة محاولة تلقائية
        await upload_with_retries(
            bot=context.bot,
            chat_id=query.message.chat_id,
            file_path=file_path,
            mode=mode,
            title=title,
        )

        try:
            await query.delete_message()
        except Exception:
            pass

    except Exception as e:
        try:
            await query.edit_message_text(f"❌ حدث خطأ أثناء التحميل أو الرفع:\n{e}")
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"❌ حدث خطأ أثناء التحميل أو الرفع:\n{e}",
            )

    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass


def main():
    if BOT_TOKEN == "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE" or not BOT_TOKEN.strip():
        print("❌ خطأ: يرجى وضع BOT_TOKEN الخاص بك في السطر 29 من الكود أولاً!")
        return

    print("🚀 جاري تشغيل البوت والاتصال بسيرفرات تلجرام...")

    # السر هنا: رفع قيمة media_write_timeout إلى 900 ثانية (15 دقيقة)
    request_config = HTTPXRequest(
        connect_timeout=120.0,
        read_timeout=900.0,
        write_timeout=900.0,
        media_write_timeout=900.0,  # مهلة رفع الفيديوهات والصوتيات
        pool_timeout=120.0,
    )

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .request(request_config)
        .get_updates_request(request_config)
        .build()
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(handle_button_click, pattern=r"^dl\|"))

    print("✅ البوت يعمل الآن بكامل طاقته! جرب إرسال رابط فيديو في تلجرام.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
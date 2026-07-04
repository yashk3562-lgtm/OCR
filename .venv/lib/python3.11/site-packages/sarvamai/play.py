import base64
import shutil
import subprocess
import wave
from .types.text_to_speech_response import TextToSpeechResponse


def is_installed(lib_name: str) -> bool:
    lib = shutil.which(lib_name)
    if lib is None:
        return False
    return True


def play(
    audio: TextToSpeechResponse, notebook: bool = False, use_ffmpeg: bool = True
) -> None:
    # Iterate through all audio chunks and concatenate them properly
    combined_audio_data = b""

    for i, audio_chunk in enumerate(audio.audios):
        # Decode each base64 chunk to get raw WAV data
        chunk_data = base64.b64decode(audio_chunk)

        if i == 0:
            # For the first chunk, keep the entire WAV file
            combined_audio_data = chunk_data
        else:
            # For subsequent chunks, find the data chunk and append only audio data
            data_pos = chunk_data.find(b"data")
            if data_pos != -1:
                # Skip the 'data' header (8 bytes: 'data' + size)
                audio_data_start = data_pos + 8
                combined_audio_data += chunk_data[audio_data_start:]

    # Update the WAV header with the correct total file size
    if len(audio.audios) > 1:
        # Update the RIFF chunk size (bytes 4-7)
        total_size = len(combined_audio_data) - 8
        combined_audio_data = (
            combined_audio_data[:4]
            + total_size.to_bytes(4, "little")
            + combined_audio_data[8:]
        )

        # Update the data chunk size (find data chunk and update its size)
        data_pos = combined_audio_data.find(b"data")
        if data_pos != -1:
            data_size = len(combined_audio_data) - data_pos - 8
            combined_audio_data = (
                combined_audio_data[: data_pos + 4]
                + data_size.to_bytes(4, "little")
                + combined_audio_data[data_pos + 8 :]
            )

    af_bytes = combined_audio_data
    if notebook:
        try:
            from IPython.display import Audio, display  # type: ignore
        except ModuleNotFoundError:
            message = "`pip install ipython` required when `notebook=False` "
            raise ValueError(message)

        display(Audio(af_bytes, rate=22050, autoplay=True))
    elif use_ffmpeg:
        if not is_installed("ffplay"):
            message = (
                "ffplay from ffmpeg not found, necessary to play audio. "
                "On mac you can install it with 'brew install ffmpeg'. "
                "On linux and windows you can install it from "
                "https://ffmpeg.org/"
            )
            raise ValueError(message)
        args = ["ffplay", "-autoexit", "-", "-nodisp"]
        proc = subprocess.Popen(
            args=args,
            stdout=subprocess.PIPE,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        out, err = proc.communicate(input=af_bytes)
        proc.poll()
    else:
        try:
            import io

            import sounddevice as sd  # type: ignore
            import soundfile as sf  # type: ignore
        except ModuleNotFoundError:
            message = (
                "`pip install sounddevice soundfile` required when "
                "`use_ffmpeg=False` "
            )
            raise ValueError(message)
        sd.play(*sf.read(io.BytesIO(af_bytes)))
        sd.wait()


def save(audio: TextToSpeechResponse, filename: str) -> None:
    if isinstance(audio.audios, list):
        # Iterate through all audio chunks and concatenate them properly
        combined_audio_data = b""

        for i, audio_chunk in enumerate(audio.audios):
            # Decode each base64 chunk to get raw WAV data
            chunk_data = base64.b64decode(audio_chunk)

            if i == 0:
                # For the first chunk, keep the entire WAV file
                combined_audio_data = chunk_data
            else:
                # For subsequent chunks, find the data chunk and append only audio data
                data_pos = chunk_data.find(b"data")
                if data_pos != -1:
                    # Skip the 'data' header (8 bytes: 'data' + size)
                    audio_data_start = data_pos + 8
                    combined_audio_data += chunk_data[audio_data_start:]

        # Update the WAV header with the correct total file size
        if len(audio.audios) > 1:
            # Update the RIFF chunk size (bytes 4-7)
            total_size = len(combined_audio_data) - 8
            combined_audio_data = (
                combined_audio_data[:4]
                + total_size.to_bytes(4, "little")
                + combined_audio_data[8:]
            )

            # Update the data chunk size (find data chunk and update its size)
            data_pos = combined_audio_data.find(b"data")
            if data_pos != -1:
                data_size = len(combined_audio_data) - data_pos - 8
                combined_audio_data = (
                    combined_audio_data[: data_pos + 4]
                    + data_size.to_bytes(4, "little")
                    + combined_audio_data[data_pos + 8 :]
                )

        with open(filename, "wb") as f:
            f.write(combined_audio_data)

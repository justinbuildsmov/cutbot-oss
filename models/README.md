Put whisper.cpp models here (they are git-ignored). The default the engine looks
for is `models/ggml-base.en.bin`; any other path works via `CUTBOT_WHISPER_MODEL`
or `--whisper-model`.

    curl -L -o models/ggml-base.en.bin \
      https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin

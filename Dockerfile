# ═══════════════════════════════════════════════════════════════
# Railway Dockerfile: Python bot + real Android APK build toolchain
# ═══════════════════════════════════════════════════════════════
FROM python:3.11-slim

# ── System deps + Java 17 (needed for Android Gradle build) ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-17-jdk-headless \
    unzip curl git wget \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV PATH="${JAVA_HOME}/bin:${PATH}"

# ── Android SDK command-line tools ──
ENV ANDROID_SDK_ROOT=/opt/android-sdk
RUN mkdir -p ${ANDROID_SDK_ROOT}/cmdline-tools && \
    cd ${ANDROID_SDK_ROOT}/cmdline-tools && \
    curl -o cmdline-tools.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip && \
    unzip -q cmdline-tools.zip && \
    mv cmdline-tools latest && \
    rm cmdline-tools.zip

ENV PATH="${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin:${ANDROID_SDK_ROOT}/platform-tools:${PATH}"

# Accept licenses + install exactly what's needed (keeps image smaller)
RUN yes | sdkmanager --licenses > /dev/null 2>&1 || true && \
    sdkmanager "platform-tools" "platforms;android-33" "build-tools;33.0.2" > /dev/null

# ── website-to-apk (WebView wrapper build tool) ──
RUN git clone --depth 1 https://github.com/Jipok/website-to-apk /opt/website-to-apk
WORKDIR /opt/website-to-apk
# Generates the one-time release keystore used to sign every built app.
# ⚠️ Default keystore password is "123456" (tool default) — for real
# production use, edit app/build.gradle's signingConfigs to set your own
# password before this step, then keep app/my-release-key.jks safe/secret.
RUN ./make.sh keygen || true

# Default app icon used when the user skips uploading a logo. Place your
# own 512x512 default_icon.png next to this Dockerfile before building.
COPY default_icon.png /opt/website-to-apk/default_icon.png

# ── Your Python Telegram bot ──
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install --with-deps chromium

COPY . .

# Tell the bot where the APK builder lives
ENV APK_BUILDER_DIR=/opt/website-to-apk

CMD ["python3", "bot.py"]

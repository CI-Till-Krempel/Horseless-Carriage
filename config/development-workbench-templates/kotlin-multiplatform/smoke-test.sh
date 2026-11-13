#!/usr/bin/env bash
# Smoke test for the kotlin-multiplatform Development Workbench template
# (US-0022).
#
# NOT run by `python3 run_tests.py` / `pytest agents/scrum_team/tests`: that
# suite runs inside the `agent` Docker Compose service, which sits on
# agent-net (internal, egress restricted to github.com/api.github.com by
# net-proxy's allowlist - see docker-compose.yaml and
# config/net-proxy/allowlist.filter). This script needs real internet
# access to dl.google.com (Android SDK command-line tools, ~130MB),
# services.gradle.org/plugins.gradle.org (Gradle wrapper + plugin
# resolution), and repo.maven.apache.org/dl.google.com's Maven repo
# (Kotlin/Android Gradle plugin dependencies) - none of which that
# sandboxed test container can reach, and this is also simply heavy
# (several hundred MB of downloads, multi-minute build) compared to the
# rest of the fast unit-test suite.
#
# Run this manually, or wire it into a dedicated, slower CI job with real
# internet access (NOT routed through net-proxy) on a host with Docker:
#   config/development-workbench-templates/kotlin-multiplatform/smoke-test.sh
#
# What it does: builds a throwaway image using this template's own
# workbench-setup.sh (installing JDK 17 + the Android SDK command-line
# tools, exactly as agent.Dockerfile's COPY+RUN does for a real project),
# scaffolds a minimal Gradle/Kotlin Android library module inside it, and
# runs its Android unit test task - proving the Android side of the
# template's install/scaffold/test path works end to end.
#
# Scope note: this intentionally does NOT attempt an iOS Gradle test task
# (`:shared:iosX64Test`) - exercising that reliably needs a full KMP
# shared-module Gradle setup (several more hundred MB of Kotlin/Native
# toolchain downloads) that's disproportionate for a smoke check. The iOS
# BUILD/RUN/DEBUG limitation this template documents (macOS + Xcode
# required) is architectural and cannot be smoke-tested at all inside any
# Linux CI runner - see workbench-setup.sh's own top-of-file comment and
# manifest.json's description for where that's surfaced instead.
set -euo pipefail
cd "$(dirname "$0")"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

cat > "$WORK/Dockerfile" <<'DOCKERFILE'
FROM python:3.11-slim
WORKDIR /app
COPY workbench-setup.sh /tmp/workbench-setup.sh
RUN chmod +x /tmp/workbench-setup.sh && /tmp/workbench-setup.sh
DOCKERFILE
cp workbench-setup.sh "$WORK/workbench-setup.sh"

docker build -t hc-smoke-kotlin-multiplatform "$WORK"

# Minimal Gradle/Kotlin Android library module - enough to prove the
# installed JDK + Android SDK + Gradle/Kotlin Android plugin can configure
# and run an Android unit test task end to end.
mkdir -p "$WORK/proj/lib/src/main/kotlin/com/example/lib"
mkdir -p "$WORK/proj/lib/src/test/kotlin/com/example/lib"

cat > "$WORK/proj/settings.gradle.kts" <<'EOF'
include(":lib")
EOF

cat > "$WORK/proj/build.gradle.kts" <<'EOF'
plugins {
    id("com.android.library") version "8.5.0" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
}
EOF

cat > "$WORK/proj/lib/build.gradle.kts" <<'EOF'
plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.example.lib"
    compileSdk = 34
    defaultConfig { minSdk = 24 }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
}
EOF

cat > "$WORK/proj/lib/src/main/AndroidManifest.xml" <<'EOF'
<manifest xmlns:android="http://schemas.android.com/apk/res/android" />
EOF

cat > "$WORK/proj/lib/src/main/kotlin/com/example/lib/Greeter.kt" <<'EOF'
package com.example.lib

class Greeter {
    fun greet(name: String): String = "Hello, $name!"
}
EOF

cat > "$WORK/proj/lib/src/test/kotlin/com/example/lib/GreeterTest.kt" <<'EOF'
package com.example.lib

import org.junit.Assert.assertEquals
import org.junit.Test

class GreeterTest {
    @Test
    fun greetsByName() {
        assertEquals("Hello, KMP!", Greeter().greet("KMP"))
    }
}
EOF

docker run --rm -v "$WORK/proj:/proj" -w /proj hc-smoke-kotlin-multiplatform sh -c '
  . /etc/profile.d/android-sdk.sh
  gradle wrapper --gradle-version 8.7 >/dev/null 2>&1 || true
  if [ -x ./gradlew ]; then
    ./gradlew :lib:testDebugUnitTest --no-daemon
  else
    echo "gradle wrapper unavailable in this throwaway image - install `gradle` or vendor a wrapper to run this smoke test." >&2
    exit 1
  fi
'
echo "kotlin-multiplatform smoke test passed: Android unit test task ran successfully."

#!/bin/sh
# Development Workbench: kotlin-multiplatform (US-0022, EP-0010)
#
# Toolchain for scaffolding a Kotlin Multiplatform (KMP) module targeting
# Android & iOS, running Gradle test tasks for both targets, and
# running/debugging the Android target - all INSIDE this Linux container.
#
# ============================================================================
# iOS LIMITATION - READ BEFORE ASSUMING iOS "JUST WORKS" HERE
# ============================================================================
# This workbench runs in a Linux container (agent.Dockerfile's base image).
# Apple does not ship Xcode, the iOS Simulator, or the iOS toolchain for
# Linux - full stop. That means:
#   - Gradle CAN configure the project's iOS targets here (iosX64,
#     iosArm64, iosSimulatorArm64 source sets resolve, Kotlin/Native
#     compiler plugins download and run) because Kotlin/Native's Linux
#     host compiler can cross-compile Kotlin/Native binaries for iOS.
#   - Kotlin/Native unit tests that don't need the simulator CAN run here,
#     e.g. `./gradlew :shared:iosX64Test` - that is genuinely iOS-target
#     test coverage executed in this Linux container.
#   - Actually BUILDING an .app/.ipa against the iOS SDK, RUNNING it on the
#     iOS Simulator or a device, or DEBUGGING it (lldb, Xcode's debugger)
#     CANNOT happen here. Tasks like
#     `:shared:embedAndSignAppleFrameworkForXcode`, any Xcode build phase,
#     `xcodebuild`, or simulator boot/launch all require a real macOS host
#     with Xcode installed - outside this agent's Docker-based environment
#     entirely.
# Do not report iOS build/run/debug as complete from inside this workbench.
# Only Android build/run/debug and Kotlin/Native iOS *test* tasks are
# genuinely done here; iOS *build/run/debug* is not, and must be handed off
# to a human (or a macOS-hosted CI runner) with Xcode. See this template's
# manifest.json description for the same statement surfaced to the
# Architect before it ever picks this template.
# ============================================================================
set -e

apt-get update
apt-get install -y --no-install-recommends openjdk-17-jdk-headless unzip curl

# Android SDK command-line tools (headless install, no Android Studio GUI).
ANDROID_SDK_ROOT=/opt/android-sdk
mkdir -p "$ANDROID_SDK_ROOT/cmdline-tools"
curl -fsSL -o /tmp/cmdline-tools.zip \
    "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
unzip -q /tmp/cmdline-tools.zip -d "$ANDROID_SDK_ROOT/cmdline-tools"
mv "$ANDROID_SDK_ROOT/cmdline-tools/cmdline-tools" "$ANDROID_SDK_ROOT/cmdline-tools/latest"
rm /tmp/cmdline-tools.zip

export ANDROID_HOME="$ANDROID_SDK_ROOT"
export PATH="$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$ANDROID_SDK_ROOT/platform-tools:$PATH"

yes | sdkmanager --licenses --sdk_root="$ANDROID_SDK_ROOT" >/dev/null 2>&1 || true
sdkmanager --sdk_root="$ANDROID_SDK_ROOT" \
    "platform-tools" "platforms;android-34" "build-tools;34.0.0" >/dev/null

{
  echo "export ANDROID_HOME=$ANDROID_SDK_ROOT"
  echo "export PATH=\$ANDROID_HOME/cmdline-tools/latest/bin:\$ANDROID_HOME/platform-tools:\$PATH"
} >> /etc/profile.d/android-sdk.sh

cat <<'EOF'
kotlin-multiplatform workbench ready (JDK 17 + Android SDK installed).
Android target: fully usable in this container. iOS target: SEE THE
"iOS LIMITATION" COMMENT AT THE TOP OF THIS SCRIPT - build/run/debug needs
a macOS host with Xcode; it is not something this container can do.

Scaffold: start from JetBrains' KMP wizard output (https://kmp.jetbrains.com)
  or `gradle init`, then declare both targets in the shared module's
  build.gradle.kts: `androidTarget()` plus
  `iosX64()`/`iosArm64()`/`iosSimulatorArm64()`.

Install:  ./gradlew tasks
  (the Gradle wrapper pulls the Kotlin Multiplatform Gradle plugin and its
  dependencies from Maven Central / Google's Maven / the Gradle Plugin
  Portal on first run - see this template's declared allowlist hosts)

Test:
  ./gradlew :shared:testDebugUnitTest   # Android unit tests
  ./gradlew :shared:iosX64Test          # Kotlin/Native iOS tests that don't
                                         # need the simulator - these DO run
                                         # in this container

Run/Debug (Android, in this container):
  ./gradlew :androidApp:installDebug
  adb shell am start -n <package>/<activity>
  # debug builds already enable JDWP (Android Gradle Plugin default) -
  # attach a JVM debugger via `adb jdwp` + `adb forward tcp:<port> jdwp:<pid>`

Run/Debug (iOS) - DOES NOT WORK IN THIS CONTAINER:
  requires a macOS host with Xcode + the iOS Simulator. Open the generated
  .xcodeproj/.xcworkspace there, or invoke
  `./gradlew :shared:embedAndSignAppleFrameworkForXcode` from Xcode's own
  build phase on that macOS host. Nothing in this Linux workbench can
  perform this step.
EOF

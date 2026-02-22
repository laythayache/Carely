cmake_minimum_required(VERSION 3.20)
project(carely-audio LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Find PipeWire
find_package(PkgConfig REQUIRED)
pkg_check_modules(PIPEWIRE REQUIRED libpipewire-0.3)

# Find WebRTC Audio Processing
pkg_check_modules(WEBRTC_APM REQUIRED webrtc-audio-processing-1)

# Source files
set(SOURCES
    src/main.cpp
    src/pipewire_capture.cpp
    src/pipewire_monitor.cpp
    src/apm_processor.cpp
    src/vad_detector.cpp
    src/amplitude_calc.cpp
    src/ipc_server.cpp
)

add_executable(carely-audio ${SOURCES})

target_include_directories(carely-audio PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${PIPEWIRE_INCLUDE_DIRS}
    ${WEBRTC_APM_INCLUDE_DIRS}
)

target_link_libraries(carely-audio PRIVATE
    ${PIPEWIRE_LIBRARIES}
    ${WEBRTC_APM_LIBRARIES}
    pthread
)

target_compile_options(carely-audio PRIVATE
    ${PIPEWIRE_CFLAGS_OTHER}
    ${WEBRTC_APM_CFLAGS_OTHER}
    -Wall -Wextra -O2
)

# Install
install(TARGETS carely-audio DESTINATION bin)

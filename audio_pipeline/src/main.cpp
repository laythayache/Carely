/**
 * Carely Audio Pipeline — Main entry point.
 *
 * Architecture:
 *   Thread 1 (RT): PipeWire mic capture → ring_buf_raw
 *   Thread 2 (RT): PipeWire monitor capture → ring_buf_ref
 *   Thread 3 (RT): APM processing (raw + ref → clean) + VAD → ring_buf_clean
 *   Thread 4:      IPC server (sends clean audio + events to Python)
 *
 * All audio is 16kHz mono S16LE. APM processes 10ms frames (160 samples).
 */

#include "pipewire_capture.h"
#include "pipewire_monitor.h"
#include "apm_processor.h"
#include "vad_detector.h"
#include "amplitude_calc.h"
#include "ipc_server.h"
#include "ring_buffer.h"

#include <pipewire/pipewire.h>
#include <cstdio>
#include <cstdlib>
#include <csignal>
#include <thread>
#include <atomic>
#include <chrono>
#include <cstring>

// Ring buffers (2s each at 16kHz)
static AudioRingBuffer ring_buf_raw;
static AudioRingBuffer ring_buf_ref;

// Global shutdown flag
static std::atomic<bool> g_running{true};

static void signal_handler(int sig) {
    (void)sig;
    g_running.store(false, std::memory_order_relaxed);
}

// Environment variable helper
static std::string get_env(const char* name, const char* fallback) {
    const char* val = std::getenv(name);
    return val ? val : fallback;
}

static int get_env_int(const char* name, int fallback) {
    const char* val = std::getenv(name);
    return val ? std::atoi(val) : fallback;
}

/**
 * APM processing thread (Thread 3).
 * Reads from ring_buf_raw and ring_buf_ref, applies APM, runs VAD,
 * sends results to the IPC server.
 */
static void apm_thread_func(APMProcessor& apm, VadDetector& vad, IPCServer& ipc) {
    int16_t raw_frame[APMProcessor::FRAME_SIZE];
    int16_t ref_frame[APMProcessor::FRAME_SIZE];
    int16_t clean_frame[APMProcessor::FRAME_SIZE];

    std::atomic<bool> capture_active{false};
    bool was_vad_started = false;

    // Register command handler for start/stop capture
    ipc.set_command_callback([&](uint8_t cmd, const uint8_t* data, uint32_t len) {
        switch (cmd) {
            case IPCServer::CMD_START_CAPTURE:
                capture_active.store(true, std::memory_order_relaxed);
                vad.reset();
                was_vad_started = false;
                fprintf(stdout, "[main] Capture started\n");
                break;
            case IPCServer::CMD_STOP_CAPTURE:
                capture_active.store(false, std::memory_order_relaxed);
                vad.reset();
                was_vad_started = false;
                fprintf(stdout, "[main] Capture stopped\n");
                break;
            case IPCServer::CMD_SET_VAD_MODE:
                if (len >= 1) {
                    fprintf(stdout, "[main] VAD mode set to %d (requires restart)\n", data[0]);
                }
                break;
            default:
                fprintf(stderr, "[main] Unknown command: 0x%02x\n", cmd);
                break;
        }
    });

    while (g_running.load(std::memory_order_relaxed)) {
        // Wait for a full frame of raw audio
        if (ring_buf_raw.available() < APMProcessor::FRAME_SIZE) {
            std::this_thread::sleep_for(std::chrono::microseconds(500));
            continue;
        }

        ring_buf_raw.pop(raw_frame, APMProcessor::FRAME_SIZE);

        // Process render reference for AEC (if available)
        if (ring_buf_ref.available() >= APMProcessor::FRAME_SIZE) {
            ring_buf_ref.pop(ref_frame, APMProcessor::FRAME_SIZE);
            apm.process_render(ref_frame);
        }

        // Apply APM to captured audio
        apm.process_capture(raw_frame, clean_frame);

        // Compute and send amplitude (always, for UI idle animation)
        float amplitude = AmplitudeCalc::compute_rms(clean_frame, APMProcessor::FRAME_SIZE);
        ipc.send_amplitude(amplitude);

        // Process VAD only when capture is active
        if (capture_active.load(std::memory_order_relaxed)) {
            VadDetector::Event event = vad.process_frame(clean_frame);

            switch (event) {
                case VadDetector::Event::VAD_START:
                    ipc.send_vad_start();
                    was_vad_started = true;
                    break;

                case VadDetector::Event::VAD_END: {
                    ipc.send_vad_end();
                    // Send the accumulated speech data
                    const auto& speech = vad.get_speech_data();
                    if (!speech.empty()) {
                        ipc.send_speech_data(speech.data(), speech.size());
                    }
                    vad.reset();
                    was_vad_started = false;
                    capture_active.store(false, std::memory_order_relaxed);
                    break;
                }

                case VadDetector::Event::NONE:
                    break;
            }

            // Also send live audio frames during capture for keyword spotter
            ipc.send_audio_frame(clean_frame, APMProcessor::FRAME_SIZE);
        }
    }
}

int main(int argc, char* argv[]) {
    (void)argc;
    (void)argv;

    // Initialize PipeWire
    pw_init(nullptr, nullptr);

    // Setup signal handlers
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    fprintf(stdout, "=== Carely Audio Pipeline ===\n");

    // Read configuration from environment
    std::string input_device = get_env("AUDIO_INPUT_DEVICE", "default");
    std::string socket_path = get_env("IPC_SOCKET_PATH", "/run/carely/audio.sock");
    int vad_aggressiveness = get_env_int("VAD_AGGRESSIVENESS", 3);
    int vad_preroll_ms = get_env_int("VAD_PREROLL_MS", 300);
    int vad_speech_start_ms = get_env_int("VAD_SPEECH_START_MS", 30);
    int vad_silence_end_ms = get_env_int("VAD_SILENCE_END_MS", 800);
    int vad_min_speech_ms = get_env_int("VAD_MIN_SPEECH_MS", 500);
    int vad_max_utterance_ms = get_env_int("VAD_MAX_UTTERANCE_MS", 30000);

    // Initialize components
    APMProcessor apm;
    if (!apm.initialize()) {
        fprintf(stderr, "Failed to initialize APM\n");
        return 1;
    }

    VadDetector::Config vad_config{
        .aggressiveness = vad_aggressiveness,
        .preroll_ms = vad_preroll_ms,
        .speech_start_ms = vad_speech_start_ms,
        .silence_end_ms = vad_silence_end_ms,
        .min_speech_ms = vad_min_speech_ms,
        .max_utterance_ms = vad_max_utterance_ms,
    };
    VadDetector vad(vad_config);
    if (!vad.initialize()) {
        fprintf(stderr, "Failed to initialize VAD\n");
        return 1;
    }

    IPCServer ipc(socket_path);
    if (!ipc.start()) {
        fprintf(stderr, "Failed to start IPC server\n");
        return 1;
    }

    // Start PipeWire capture (mic input)
    PipeWireCapture capture(ring_buf_raw, input_device);
    if (!capture.start()) {
        fprintf(stderr, "Failed to start PipeWire capture\n");
        return 1;
    }

    // Start PipeWire monitor (speaker output for AEC)
    AudioRingBuffer ring_buf_ref_local;
    PipeWireMonitor monitor(ring_buf_ref, "default");
    if (!monitor.start()) {
        // Monitor is optional — AEC won't work but we can continue
        fprintf(stderr, "Warning: Failed to start PipeWire monitor (AEC disabled)\n");
    }

    // Start APM processing thread
    std::thread apm_thread(apm_thread_func, std::ref(apm), std::ref(vad), std::ref(ipc));

    // Notify Python that pipeline is ready
    ipc.send_pipeline_ready();
    fprintf(stdout, "[main] Pipeline ready, waiting for commands...\n");

    // Main thread: just wait for shutdown
    while (g_running.load(std::memory_order_relaxed)) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    fprintf(stdout, "\n[main] Shutting down...\n");

    // Cleanup
    capture.stop();
    monitor.stop();

    if (apm_thread.joinable()) apm_thread.join();

    ipc.stop();
    pw_deinit();

    fprintf(stdout, "[main] Shutdown complete\n");
    return 0;
}

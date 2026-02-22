/**
 * PipeWire audio capture for microphone input.
 * Captures 16kHz mono int16 audio from the configured input device.
 * Writes frames into a ring buffer for processing.
 */

#pragma once

#include "ring_buffer.h"
#include <pipewire/pipewire.h>
#include <spa/param/audio/format-utils.h>
#include <cstdint>
#include <string>
#include <atomic>

// 2 seconds of audio at 16kHz = 32768 samples (power of 2)
using AudioRingBuffer = RingBuffer<int16_t, 32768>;

class PipeWireCapture {
public:
    PipeWireCapture(AudioRingBuffer& ring_buffer, const std::string& device_name = "default");
    ~PipeWireCapture();

    // Non-copyable
    PipeWireCapture(const PipeWireCapture&) = delete;
    PipeWireCapture& operator=(const PipeWireCapture&) = delete;

    bool start();
    void stop();
    bool is_running() const { return running_.load(std::memory_order_relaxed); }

    static constexpr int SAMPLE_RATE = 16000;
    static constexpr int CHANNELS = 1;
    static constexpr int FRAME_SIZE_SAMPLES = 160;  // 10ms at 16kHz

private:
    static void on_process(void* userdata);
    static void on_stream_state_changed(void* userdata, enum pw_stream_state old,
                                         enum pw_stream_state state, const char* error);

    AudioRingBuffer& ring_buffer_;
    std::string device_name_;
    std::atomic<bool> running_{false};

    struct pw_thread_loop* loop_ = nullptr;
    struct pw_stream* stream_ = nullptr;
};

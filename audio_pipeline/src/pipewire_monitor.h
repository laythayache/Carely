/**
 * PipeWire monitor source capture for AEC render reference.
 * Captures the mixed audio output (what the speaker is playing)
 * to feed into WebRTC APM's ProcessReverseStream().
 */

#pragma once

#include "ring_buffer.h"
#include <pipewire/pipewire.h>
#include <cstdint>
#include <string>
#include <atomic>

using AudioRingBuffer = RingBuffer<int16_t, 32768>;

class PipeWireMonitor {
public:
    PipeWireMonitor(AudioRingBuffer& ring_buffer, const std::string& device_name = "default");
    ~PipeWireMonitor();

    PipeWireMonitor(const PipeWireMonitor&) = delete;
    PipeWireMonitor& operator=(const PipeWireMonitor&) = delete;

    bool start();
    void stop();
    bool is_running() const { return running_.load(std::memory_order_relaxed); }

    static constexpr int SAMPLE_RATE = 16000;
    static constexpr int CHANNELS = 1;

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

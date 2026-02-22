/**
 * PipeWire monitor source capture implementation.
 * Captures the speaker output for AEC reference.
 */

#include "pipewire_monitor.h"
#include <cstdio>
#include <cstring>
#include <spa/param/audio/format-utils.h>

static const struct pw_stream_events monitor_stream_events = {
    .version = PW_VERSION_STREAM_EVENTS,
    .state_changed = PipeWireMonitor::on_stream_state_changed,
    .process = PipeWireMonitor::on_process,
};

PipeWireMonitor::PipeWireMonitor(AudioRingBuffer& ring_buffer, const std::string& device_name)
    : ring_buffer_(ring_buffer), device_name_(device_name) {
}

PipeWireMonitor::~PipeWireMonitor() {
    stop();
}

bool PipeWireMonitor::start() {
    loop_ = pw_thread_loop_new("monitor", nullptr);
    if (!loop_) {
        fprintf(stderr, "[monitor] Failed to create PipeWire thread loop\n");
        return false;
    }

    auto* props = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Audio",
        PW_KEY_MEDIA_CATEGORY, "Capture",
        PW_KEY_MEDIA_ROLE, "Communication",
        // Capture the monitor of the default sink (speaker output)
        PW_KEY_STREAM_CAPTURE_SINK, "true",
        nullptr
    );

    stream_ = pw_stream_new_simple(
        pw_thread_loop_get_loop(loop_),
        "carely-aec-monitor",
        props,
        &monitor_stream_events,
        this
    );

    if (!stream_) {
        fprintf(stderr, "[monitor] Failed to create PipeWire stream\n");
        pw_thread_loop_destroy(loop_);
        loop_ = nullptr;
        return false;
    }

    uint8_t buffer[1024];
    struct spa_pod_builder b = SPA_POD_BUILDER_INIT(buffer, sizeof(buffer));

    struct spa_audio_info_raw audio_info = {};
    audio_info.format = SPA_AUDIO_FORMAT_S16_LE;
    audio_info.rate = SAMPLE_RATE;
    audio_info.channels = CHANNELS;

    const struct spa_pod* params[1];
    params[0] = spa_format_audio_raw_build(&b, SPA_PARAM_EnumFormat, &audio_info);

    int res = pw_stream_connect(
        stream_,
        PW_DIRECTION_INPUT,
        PW_ID_ANY,
        static_cast<pw_stream_flags>(
            PW_STREAM_FLAG_AUTOCONNECT |
            PW_STREAM_FLAG_MAP_BUFFERS |
            PW_STREAM_FLAG_RT_PROCESS
        ),
        params, 1
    );

    if (res < 0) {
        fprintf(stderr, "[monitor] Failed to connect stream: %s\n", strerror(-res));
        pw_stream_destroy(stream_);
        pw_thread_loop_destroy(loop_);
        stream_ = nullptr;
        loop_ = nullptr;
        return false;
    }

    pw_thread_loop_start(loop_);
    running_.store(true, std::memory_order_relaxed);
    fprintf(stdout, "[monitor] Started (AEC render reference)\n");
    return true;
}

void PipeWireMonitor::stop() {
    running_.store(false, std::memory_order_relaxed);
    if (loop_) {
        pw_thread_loop_stop(loop_);
    }
    if (stream_) {
        pw_stream_destroy(stream_);
        stream_ = nullptr;
    }
    if (loop_) {
        pw_thread_loop_destroy(loop_);
        loop_ = nullptr;
    }
    fprintf(stdout, "[monitor] Stopped\n");
}

void PipeWireMonitor::on_process(void* userdata) {
    auto* self = static_cast<PipeWireMonitor*>(userdata);
    struct pw_buffer* buf = pw_stream_dequeue_buffer(self->stream_);
    if (!buf) return;

    struct spa_buffer* spa_buf = buf->buffer;
    if (!spa_buf->datas[0].data) {
        pw_stream_queue_buffer(self->stream_, buf);
        return;
    }

    auto* samples = static_cast<int16_t*>(spa_buf->datas[0].data);
    uint32_t n_bytes = spa_buf->datas[0].chunk->size;
    uint32_t n_samples = n_bytes / sizeof(int16_t);

    if (!self->ring_buffer_.push(samples, n_samples)) {
        // Overflow is less critical for monitor (AEC degrades gracefully)
    }

    pw_stream_queue_buffer(self->stream_, buf);
}

void PipeWireMonitor::on_stream_state_changed(void* userdata, enum pw_stream_state old,
                                                enum pw_stream_state state, const char* error) {
    (void)userdata;
    fprintf(stdout, "[monitor] State: %s -> %s",
            pw_stream_state_as_string(old),
            pw_stream_state_as_string(state));
    if (error) {
        fprintf(stderr, " (error: %s)", error);
    }
    fprintf(stdout, "\n");
}

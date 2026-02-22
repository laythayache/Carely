/**
 * PipeWire audio capture implementation.
 */

#include "pipewire_capture.h"
#include <cstdio>
#include <cstring>
#include <spa/param/audio/format-utils.h>

static const struct pw_stream_events stream_events = {
    .version = PW_VERSION_STREAM_EVENTS,
    .state_changed = PipeWireCapture::on_stream_state_changed,
    .process = PipeWireCapture::on_process,
};

PipeWireCapture::PipeWireCapture(AudioRingBuffer& ring_buffer, const std::string& device_name)
    : ring_buffer_(ring_buffer), device_name_(device_name) {
}

PipeWireCapture::~PipeWireCapture() {
    stop();
}

bool PipeWireCapture::start() {
    loop_ = pw_thread_loop_new("capture", nullptr);
    if (!loop_) {
        fprintf(stderr, "[capture] Failed to create PipeWire thread loop\n");
        return false;
    }

    auto* props = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Audio",
        PW_KEY_MEDIA_CATEGORY, "Capture",
        PW_KEY_MEDIA_ROLE, "Communication",
        nullptr
    );

    if (device_name_ != "default") {
        pw_properties_set(props, PW_KEY_TARGET_OBJECT, device_name_.c_str());
    }

    stream_ = pw_stream_new_simple(
        pw_thread_loop_get_loop(loop_),
        "carely-mic-capture",
        props,
        &stream_events,
        this
    );

    if (!stream_) {
        fprintf(stderr, "[capture] Failed to create PipeWire stream\n");
        pw_thread_loop_destroy(loop_);
        loop_ = nullptr;
        return false;
    }

    // Audio format: 16kHz mono S16LE
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
        fprintf(stderr, "[capture] Failed to connect stream: %s\n", strerror(-res));
        pw_stream_destroy(stream_);
        pw_thread_loop_destroy(loop_);
        stream_ = nullptr;
        loop_ = nullptr;
        return false;
    }

    pw_thread_loop_start(loop_);
    running_.store(true, std::memory_order_relaxed);
    fprintf(stdout, "[capture] Started (16kHz mono S16LE)\n");
    return true;
}

void PipeWireCapture::stop() {
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
    fprintf(stdout, "[capture] Stopped\n");
}

void PipeWireCapture::on_process(void* userdata) {
    auto* self = static_cast<PipeWireCapture*>(userdata);
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

    // Push samples into the ring buffer
    if (!self->ring_buffer_.push(samples, n_samples)) {
        // Buffer overflow - audio pipeline is too slow
        fprintf(stderr, "[capture] Ring buffer overflow, dropping %u samples\n", n_samples);
    }

    pw_stream_queue_buffer(self->stream_, buf);
}

void PipeWireCapture::on_stream_state_changed(void* userdata, enum pw_stream_state old,
                                                enum pw_stream_state state, const char* error) {
    (void)userdata;
    fprintf(stdout, "[capture] State: %s -> %s",
            pw_stream_state_as_string(old),
            pw_stream_state_as_string(state));
    if (error) {
        fprintf(stderr, " (error: %s)", error);
    }
    fprintf(stdout, "\n");
}

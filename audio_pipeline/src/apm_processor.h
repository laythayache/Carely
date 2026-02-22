/**
 * WebRTC Audio Processing Module (APM) wrapper.
 * Applies AEC, Noise Suppression, and Automatic Gain Control
 * to raw microphone audio using the speaker output as AEC reference.
 */

#pragma once

#include <cstdint>
#include <memory>

// Forward declare WebRTC types
namespace webrtc {
class AudioProcessing;
class StreamConfig;
}

class APMProcessor {
public:
    APMProcessor();
    ~APMProcessor();

    APMProcessor(const APMProcessor&) = delete;
    APMProcessor& operator=(const APMProcessor&) = delete;

    bool initialize();

    /**
     * Process a 10ms frame of captured audio.
     * @param input  160 samples (10ms at 16kHz mono, int16)
     * @param output 160 samples of processed audio
     */
    void process_capture(const int16_t* input, int16_t* output);

    /**
     * Feed a 10ms frame of render reference (speaker output) for AEC.
     * @param render_frame 160 samples of what the speaker is playing
     */
    void process_render(const int16_t* render_frame);

    static constexpr int SAMPLE_RATE = 16000;
    static constexpr int CHANNELS = 1;
    static constexpr int FRAME_SIZE = 160;  // 10ms at 16kHz

private:
    std::unique_ptr<webrtc::AudioProcessing> apm_;
    std::unique_ptr<webrtc::StreamConfig> stream_config_;
};

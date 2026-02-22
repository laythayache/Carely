/**
 * Voice Activity Detection using WebRTC VAD.
 * Implements pre-roll buffer, speech start/end logic,
 * min speech duration, max utterance cap.
 */

#pragma once

#include <cstdint>
#include <vector>
#include <functional>

class VadDetector {
public:
    struct Config {
        int aggressiveness = 3;        // 0-3, higher = more aggressive filtering
        int preroll_ms = 300;          // Pre-roll buffer size in ms
        int speech_start_ms = 30;      // Consecutive voiced frames to trigger start
        int silence_end_ms = 800;      // Consecutive silent frames to trigger end
        int min_speech_ms = 500;       // Discard segments shorter than this
        int max_utterance_ms = 30000;  // Force-end after this duration
    };

    enum class Event {
        NONE,
        VAD_START,
        VAD_END,
    };

    // Callback signature: (event, pcm_data_ptr, pcm_data_size_bytes)
    // For VAD_END, pcm_data contains the full speech segment (including pre-roll)
    using EventCallback = std::function<void(Event, const int16_t*, size_t)>;

    explicit VadDetector(const Config& config);
    ~VadDetector();

    VadDetector(const VadDetector&) = delete;
    VadDetector& operator=(const VadDetector&) = delete;

    bool initialize();

    /**
     * Process a 10ms frame (160 samples at 16kHz).
     * May trigger VAD_START or VAD_END events via callback.
     */
    Event process_frame(const int16_t* frame);

    /**
     * Get the accumulated speech data after VAD_END.
     * Valid only immediately after process_frame returns VAD_END.
     */
    const std::vector<int16_t>& get_speech_data() const { return speech_buffer_; }

    /**
     * Reset VAD state (call when cancelling a listen session).
     */
    void reset();

    /**
     * Force-end the current utterance (for long-press).
     * Returns true if there was speech data to emit.
     */
    bool force_end();

    static constexpr int FRAME_SIZE = 160;  // 10ms at 16kHz
    static constexpr int SAMPLE_RATE = 16000;

private:
    enum class InternalState {
        WAITING,   // No speech detected yet
        SPEAKING,  // Speech in progress
        DONE,      // Speech segment complete
    };

    Config config_;
    void* vad_handle_ = nullptr;  // WebRTC VAD handle (opaque)
    InternalState state_ = InternalState::WAITING;

    // Pre-roll circular buffer
    std::vector<int16_t> preroll_buffer_;
    size_t preroll_write_pos_ = 0;
    size_t preroll_capacity_ = 0;  // In samples

    // Speech accumulation buffer
    std::vector<int16_t> speech_buffer_;

    // Counters (in 10ms frames)
    int voiced_frame_count_ = 0;
    int silence_frame_count_ = 0;
    int total_speech_frames_ = 0;

    // Thresholds (in frames)
    int speech_start_frames_ = 0;
    int silence_end_frames_ = 0;
    int min_speech_frames_ = 0;
    int max_utterance_frames_ = 0;
};

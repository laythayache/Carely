/**
 * VAD detector implementation using WebRTC VAD API.
 */

#include "vad_detector.h"
#include <cstdio>
#include <cstring>
#include <algorithm>

// WebRTC VAD C API
extern "C" {
    typedef struct WebRtcVadInst VadInst;
    VadInst* WebRtcVad_Create(void);
    int WebRtcVad_Init(VadInst* handle);
    int WebRtcVad_set_mode(VadInst* handle, int mode);
    int WebRtcVad_Process(VadInst* handle, int fs, const int16_t* audio_frame,
                          size_t frame_length);
    void WebRtcVad_Free(VadInst* handle);
}

VadDetector::VadDetector(const Config& config) : config_(config) {
    // Convert ms thresholds to frame counts (1 frame = 10ms)
    speech_start_frames_ = config.speech_start_ms / 10;
    silence_end_frames_ = config.silence_end_ms / 10;
    min_speech_frames_ = config.min_speech_ms / 10;
    max_utterance_frames_ = config.max_utterance_ms / 10;

    // Pre-roll buffer capacity in samples
    preroll_capacity_ = (config.preroll_ms / 10) * FRAME_SIZE;
    preroll_buffer_.resize(preroll_capacity_, 0);
}

VadDetector::~VadDetector() {
    if (vad_handle_) {
        WebRtcVad_Free(static_cast<VadInst*>(vad_handle_));
    }
}

bool VadDetector::initialize() {
    auto* inst = WebRtcVad_Create();
    if (!inst) {
        fprintf(stderr, "[vad] Failed to create VAD instance\n");
        return false;
    }

    if (WebRtcVad_Init(inst) != 0) {
        fprintf(stderr, "[vad] Failed to initialize VAD\n");
        WebRtcVad_Free(inst);
        return false;
    }

    if (WebRtcVad_set_mode(inst, config_.aggressiveness) != 0) {
        fprintf(stderr, "[vad] Failed to set aggressiveness to %d\n", config_.aggressiveness);
        WebRtcVad_Free(inst);
        return false;
    }

    vad_handle_ = inst;
    fprintf(stdout, "[vad] Initialized (aggressiveness=%d, silence_end=%dms)\n",
            config_.aggressiveness, config_.silence_end_ms);
    return true;
}

VadDetector::Event VadDetector::process_frame(const int16_t* frame) {
    if (!vad_handle_) return Event::NONE;

    auto* inst = static_cast<VadInst*>(vad_handle_);
    int is_voiced = WebRtcVad_Process(inst, SAMPLE_RATE, frame, FRAME_SIZE);

    switch (state_) {
        case InternalState::WAITING: {
            // Always update pre-roll buffer
            std::memcpy(&preroll_buffer_[preroll_write_pos_], frame, FRAME_SIZE * sizeof(int16_t));
            preroll_write_pos_ = (preroll_write_pos_ + FRAME_SIZE) % preroll_capacity_;

            if (is_voiced) {
                voiced_frame_count_++;
                if (voiced_frame_count_ >= speech_start_frames_) {
                    // Speech detected — transition to SPEAKING
                    state_ = InternalState::SPEAKING;
                    total_speech_frames_ = 0;
                    silence_frame_count_ = 0;

                    // Copy pre-roll buffer into speech buffer
                    speech_buffer_.clear();
                    speech_buffer_.reserve(preroll_capacity_ + SAMPLE_RATE * 30);  // Reserve 30s

                    // Read pre-roll from the circular buffer
                    size_t read_pos = preroll_write_pos_;
                    for (size_t i = 0; i < preroll_capacity_; i += FRAME_SIZE) {
                        speech_buffer_.insert(speech_buffer_.end(),
                                            &preroll_buffer_[read_pos],
                                            &preroll_buffer_[read_pos] + FRAME_SIZE);
                        read_pos = (read_pos + FRAME_SIZE) % preroll_capacity_;
                    }

                    // Also add current frame
                    speech_buffer_.insert(speech_buffer_.end(), frame, frame + FRAME_SIZE);
                    total_speech_frames_++;

                    return Event::VAD_START;
                }
            } else {
                voiced_frame_count_ = 0;
            }
            break;
        }

        case InternalState::SPEAKING: {
            // Accumulate audio
            speech_buffer_.insert(speech_buffer_.end(), frame, frame + FRAME_SIZE);
            total_speech_frames_++;

            if (is_voiced) {
                silence_frame_count_ = 0;
            } else {
                silence_frame_count_++;
            }

            // Check for end conditions
            bool silence_end = silence_frame_count_ >= silence_end_frames_;
            bool max_duration = total_speech_frames_ >= max_utterance_frames_;

            if (silence_end || max_duration) {
                if (max_duration) {
                    fprintf(stdout, "[vad] Max utterance reached (%ds)\n",
                            config_.max_utterance_ms / 1000);
                }

                // Check minimum speech duration
                if (total_speech_frames_ >= min_speech_frames_) {
                    state_ = InternalState::DONE;
                    return Event::VAD_END;
                } else {
                    // Too short — discard and reset
                    fprintf(stdout, "[vad] Speech too short (%dms), discarding\n",
                            total_speech_frames_ * 10);
                    reset();
                }
            }
            break;
        }

        case InternalState::DONE:
            // Waiting for reset() to be called
            break;
    }

    return Event::NONE;
}

void VadDetector::reset() {
    state_ = InternalState::WAITING;
    voiced_frame_count_ = 0;
    silence_frame_count_ = 0;
    total_speech_frames_ = 0;
    speech_buffer_.clear();
}

bool VadDetector::force_end() {
    if (state_ == InternalState::SPEAKING && total_speech_frames_ >= min_speech_frames_) {
        state_ = InternalState::DONE;
        return true;
    }
    return false;
}

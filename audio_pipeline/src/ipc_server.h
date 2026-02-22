/**
 * Unix domain socket IPC server.
 * Sends audio frames, VAD events, and amplitude data to the Python orchestrator.
 * Receives control messages (start/stop capture, set VAD mode).
 *
 * Protocol:
 *   Message = type(1B) + length(4B, little-endian) + payload(length bytes)
 *
 * Message types (server → client):
 *   0x01 AUDIO_FRAME:    160 int16 samples (320 bytes)
 *   0x02 VAD_START:      0 bytes
 *   0x03 VAD_END:        0 bytes
 *   0x04 AMPLITUDE:      4 bytes (float32)
 *   0x05 PIPELINE_READY: 0 bytes
 *   0x06 PIPELINE_ERROR: N bytes (UTF-8 string)
 *
 * Message types (client → server):
 *   0x80 START_CAPTURE:  0 bytes
 *   0x81 STOP_CAPTURE:   0 bytes
 *   0x82 SET_VAD_MODE:   1 byte (aggressiveness 0-3)
 */

#pragma once

#include <cstdint>
#include <string>
#include <atomic>
#include <functional>
#include <thread>

class IPCServer {
public:
    static constexpr uint8_t MSG_AUDIO_FRAME    = 0x01;
    static constexpr uint8_t MSG_VAD_START      = 0x02;
    static constexpr uint8_t MSG_VAD_END        = 0x03;
    static constexpr uint8_t MSG_AMPLITUDE      = 0x04;
    static constexpr uint8_t MSG_PIPELINE_READY = 0x05;
    static constexpr uint8_t MSG_PIPELINE_ERROR = 0x06;

    static constexpr uint8_t CMD_START_CAPTURE  = 0x80;
    static constexpr uint8_t CMD_STOP_CAPTURE   = 0x81;
    static constexpr uint8_t CMD_SET_VAD_MODE   = 0x82;

    using CommandCallback = std::function<void(uint8_t cmd, const uint8_t* data, uint32_t len)>;

    explicit IPCServer(const std::string& socket_path);
    ~IPCServer();

    IPCServer(const IPCServer&) = delete;
    IPCServer& operator=(const IPCServer&) = delete;

    bool start();
    void stop();

    void set_command_callback(CommandCallback cb) { command_callback_ = std::move(cb); }

    // Send messages to the Python client
    bool send_audio_frame(const int16_t* frame, int count);
    bool send_vad_start();
    bool send_vad_end();
    bool send_amplitude(float rms);
    bool send_pipeline_ready();
    bool send_pipeline_error(const std::string& error);

    // Send raw VAD speech data (full utterance after VAD_END)
    bool send_speech_data(const int16_t* data, size_t sample_count);

    bool has_client() const { return client_fd_.load(std::memory_order_relaxed) >= 0; }

private:
    bool send_message(uint8_t type, const void* payload, uint32_t length);
    void accept_loop();
    void read_loop(int client_fd);

    std::string socket_path_;
    int server_fd_ = -1;
    std::atomic<int> client_fd_{-1};
    std::atomic<bool> running_{false};
    CommandCallback command_callback_;
    std::thread accept_thread_;
    std::thread read_thread_;
};

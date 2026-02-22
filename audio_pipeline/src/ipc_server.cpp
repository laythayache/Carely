/**
 * IPC server implementation using Unix domain sockets.
 */

#include "ipc_server.h"
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <cstdio>
#include <cstring>
#include <cerrno>

IPCServer::IPCServer(const std::string& socket_path)
    : socket_path_(socket_path) {
}

IPCServer::~IPCServer() {
    stop();
}

bool IPCServer::start() {
    // Remove existing socket file
    unlink(socket_path_.c_str());

    server_fd_ = socket(AF_UNIX, SOCK_STREAM, 0);
    if (server_fd_ < 0) {
        fprintf(stderr, "[ipc] Failed to create socket: %s\n", strerror(errno));
        return false;
    }

    struct sockaddr_un addr = {};
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, socket_path_.c_str(), sizeof(addr.sun_path) - 1);

    if (bind(server_fd_, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr)) < 0) {
        fprintf(stderr, "[ipc] Failed to bind socket: %s\n", strerror(errno));
        close(server_fd_);
        server_fd_ = -1;
        return false;
    }

    if (listen(server_fd_, 1) < 0) {
        fprintf(stderr, "[ipc] Failed to listen: %s\n", strerror(errno));
        close(server_fd_);
        server_fd_ = -1;
        return false;
    }

    running_.store(true, std::memory_order_relaxed);
    accept_thread_ = std::thread(&IPCServer::accept_loop, this);

    fprintf(stdout, "[ipc] Server listening on %s\n", socket_path_.c_str());
    return true;
}

void IPCServer::stop() {
    running_.store(false, std::memory_order_relaxed);

    int cfd = client_fd_.exchange(-1, std::memory_order_relaxed);
    if (cfd >= 0) {
        shutdown(cfd, SHUT_RDWR);
        close(cfd);
    }

    if (server_fd_ >= 0) {
        shutdown(server_fd_, SHUT_RDWR);
        close(server_fd_);
        server_fd_ = -1;
    }

    if (accept_thread_.joinable()) accept_thread_.join();
    if (read_thread_.joinable()) read_thread_.join();

    unlink(socket_path_.c_str());
    fprintf(stdout, "[ipc] Server stopped\n");
}

void IPCServer::accept_loop() {
    while (running_.load(std::memory_order_relaxed)) {
        struct sockaddr_un client_addr = {};
        socklen_t addr_len = sizeof(client_addr);

        int cfd = accept(server_fd_, reinterpret_cast<struct sockaddr*>(&client_addr), &addr_len);
        if (cfd < 0) {
            if (running_.load(std::memory_order_relaxed)) {
                fprintf(stderr, "[ipc] Accept failed: %s\n", strerror(errno));
            }
            continue;
        }

        // Close any existing client
        int old_cfd = client_fd_.exchange(cfd, std::memory_order_relaxed);
        if (old_cfd >= 0) {
            close(old_cfd);
        }

        fprintf(stdout, "[ipc] Client connected\n");

        // Wait for read thread to finish if running
        if (read_thread_.joinable()) read_thread_.join();

        // Start reading commands from client
        read_thread_ = std::thread(&IPCServer::read_loop, this, cfd);
    }
}

void IPCServer::read_loop(int client_fd) {
    uint8_t header[5];

    while (running_.load(std::memory_order_relaxed) &&
           client_fd_.load(std::memory_order_relaxed) == client_fd) {

        // Read header: type(1) + length(4)
        ssize_t n = recv(client_fd, header, sizeof(header), MSG_WAITALL);
        if (n <= 0) {
            if (n == 0) {
                fprintf(stdout, "[ipc] Client disconnected\n");
            } else if (running_.load(std::memory_order_relaxed)) {
                fprintf(stderr, "[ipc] Read error: %s\n", strerror(errno));
            }
            client_fd_.compare_exchange_strong(client_fd, -1, std::memory_order_relaxed);
            break;
        }

        uint8_t type = header[0];
        uint32_t length;
        std::memcpy(&length, &header[1], 4);  // Little-endian assumed (x86)

        // Read payload
        std::vector<uint8_t> payload(length);
        if (length > 0) {
            ssize_t payload_read = recv(client_fd, payload.data(), length, MSG_WAITALL);
            if (payload_read != static_cast<ssize_t>(length)) {
                fprintf(stderr, "[ipc] Incomplete payload read\n");
                break;
            }
        }

        if (command_callback_) {
            command_callback_(type, payload.data(), length);
        }
    }
}

bool IPCServer::send_message(uint8_t type, const void* payload, uint32_t length) {
    int cfd = client_fd_.load(std::memory_order_relaxed);
    if (cfd < 0) return false;

    // Build message: type(1) + length(4) + payload
    uint8_t header[5];
    header[0] = type;
    std::memcpy(&header[1], &length, 4);

    // Send header
    ssize_t n = send(cfd, header, sizeof(header), MSG_NOSIGNAL);
    if (n != sizeof(header)) return false;

    // Send payload
    if (length > 0 && payload) {
        n = send(cfd, payload, length, MSG_NOSIGNAL);
        if (n != static_cast<ssize_t>(length)) return false;
    }

    return true;
}

bool IPCServer::send_audio_frame(const int16_t* frame, int count) {
    return send_message(MSG_AUDIO_FRAME, frame, count * sizeof(int16_t));
}

bool IPCServer::send_vad_start() {
    return send_message(MSG_VAD_START, nullptr, 0);
}

bool IPCServer::send_vad_end() {
    return send_message(MSG_VAD_END, nullptr, 0);
}

bool IPCServer::send_amplitude(float rms) {
    return send_message(MSG_AMPLITUDE, &rms, sizeof(float));
}

bool IPCServer::send_pipeline_ready() {
    return send_message(MSG_PIPELINE_READY, nullptr, 0);
}

bool IPCServer::send_pipeline_error(const std::string& error) {
    return send_message(MSG_PIPELINE_ERROR, error.data(), error.size());
}

bool IPCServer::send_speech_data(const int16_t* data, size_t sample_count) {
    return send_message(MSG_AUDIO_FRAME, data, sample_count * sizeof(int16_t));
}

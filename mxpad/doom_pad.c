// doomgeneric video/input for the MX Keypad easter egg.
// Linked with ozkl/doomgeneric (GPLv2).

#include "doomgeneric.h"
#include "doomkeys.h"

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#define KEYQUEUE_SIZE 32
#define MAGIC 0x44504D58u /* XMPD */

struct FrameHeader {
    uint32_t magic;
    uint32_t seq;
    uint16_t width;
    uint16_t height;
    uint16_t bpp;
    uint16_t reserved;
};

static unsigned short s_KeyQueue[KEYQUEUE_SIZE];
static unsigned int s_KeyQueueWriteIndex;
static unsigned int s_KeyQueueReadIndex;
static struct timeval s_Start;
static struct FrameHeader *s_Header;
static size_t s_MapSize;

static void add_key(int pressed, unsigned char key)
{
    unsigned short data = (unsigned short)((pressed ? 1 : 0) << 8) | key;
    s_KeyQueue[s_KeyQueueWriteIndex] = data;
    s_KeyQueueWriteIndex = (s_KeyQueueWriteIndex + 1) % KEYQUEUE_SIZE;
}

static void drain_stdin(void)
{
    unsigned char buf[2];
    for (;;) {
        ssize_t n = read(STDIN_FILENO, buf, 2);
        if (n < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                break;
            }
            break;
        }
        if (n == 0) {
            break;
        }
        if (n == 2) {
            add_key(buf[0] != 0, buf[1]);
        }
    }
}

void DG_Init(void)
{
    const char *path = getenv("MXPAD_DOOM_FB");
    if (path == NULL || path[0] == '\0') {
        fprintf(stderr, "mxpad-doom: set MXPAD_DOOM_FB to a frame file\n");
        exit(1);
    }

    s_MapSize = sizeof(struct FrameHeader)
        + (size_t)DOOMGENERIC_RESX * (size_t)DOOMGENERIC_RESY * 4u;

    int fd = open(path, O_RDWR | O_CREAT, 0600);
    if (fd < 0) {
        perror("mxpad-doom: open frame file");
        exit(1);
    }
    if (ftruncate(fd, (off_t)s_MapSize) != 0) {
        perror("mxpad-doom: ftruncate");
        exit(1);
    }
    void *map = mmap(NULL, s_MapSize, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (map == MAP_FAILED) {
        perror("mxpad-doom: mmap");
        exit(1);
    }
    close(fd);
    s_Header = (struct FrameHeader *)map;
    memset(s_Header, 0, s_MapSize);
    s_Header->magic = MAGIC;
    s_Header->width = DOOMGENERIC_RESX;
    s_Header->height = DOOMGENERIC_RESY;
    s_Header->bpp = 32;

    int flags = fcntl(STDIN_FILENO, F_GETFL, 0);
    if (flags >= 0) {
        fcntl(STDIN_FILENO, F_SETFL, flags | O_NONBLOCK);
    }
    gettimeofday(&s_Start, NULL);
}

void DG_DrawFrame(void)
{
    drain_stdin();
    if (s_Header == NULL || DG_ScreenBuffer == NULL) {
        return;
    }
    uint8_t *pixels = (uint8_t *)(s_Header + 1);
    memcpy(pixels, DG_ScreenBuffer,
           (size_t)DOOMGENERIC_RESX * (size_t)DOOMGENERIC_RESY * 4u);
    s_Header->seq += 1;
    msync(s_Header, s_MapSize, MS_ASYNC);
}

void DG_SleepMs(uint32_t ms)
{
    drain_stdin();
    struct timespec ts;
    ts.tv_sec = (time_t)(ms / 1000);
    ts.tv_nsec = (long)(ms % 1000) * 1000000L;
    nanosleep(&ts, NULL);
}

uint32_t DG_GetTicksMs(void)
{
    struct timeval now;
    gettimeofday(&now, NULL);
    uint32_t ticks = (uint32_t)((now.tv_sec - s_Start.tv_sec) * 1000);
    ticks += (uint32_t)((now.tv_usec - s_Start.tv_usec) / 1000);
    return ticks;
}

int DG_GetKey(int *pressed, unsigned char *doomKey)
{
    drain_stdin();
    if (s_KeyQueueReadIndex == s_KeyQueueWriteIndex) {
        return 0;
    }
    unsigned short data = s_KeyQueue[s_KeyQueueReadIndex];
    s_KeyQueueReadIndex = (s_KeyQueueReadIndex + 1) % KEYQUEUE_SIZE;
    *pressed = data >> 8;
    *doomKey = (unsigned char)(data & 0xFF);
    return 1;
}

void DG_SetWindowTitle(const char *title)
{
    (void)title;
}

int main(int argc, char **argv)
{
    doomgeneric_Create(argc, argv);
    for (;;) {
        doomgeneric_Tick();
    }
    return 0;
}

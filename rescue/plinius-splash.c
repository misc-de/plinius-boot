// plinius-splash FONT STATUS TITLE [LINE...]: the screen of the rescue system (rescue/rescue-init).
// Draws the Arch logo as ASCII art (in PLINIUS_SPLASH_COLOR, RRGGBB, if set), TITLE below it, the
// LINEs and a status line (uptime and the last entry of the log file STATUS) on the first connected
// display. The status line is redrawn every second, so a picture that stops changing means a hung
// phone, not a busy one.
// FONT is a PSF1 or PSF2 console font. Plain DRM ioctls (dumb buffers, legacy modesetting), no libdrm:
// the rescue system has only glibc. The picture stays up only while the program holds the
// device, so it runs until it is killed.
// SPDX-License-Identifier: MIT
#include <drm/drm.h>
#include <drm/drm_mode.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

// The Arch Linux logo in the shape neofetch draws it
static const char *const logo[] = {
	"                  -`",
	"                 .o+`",
	"                `ooo/",
	"               `+oooo:",
	"              `+oooooo:",
	"              -+oooooo+:",
	"            `/:-:++oooo+:",
	"           `/++++/+++++++:",
	"          `/++++++++++++++:",
	"         `/+++ooooooooooooo/`",
	"        ./ooosssso++osssssso+`",
	"       .oossssso-````/ossssss+`",
	"      -osssssso.      :ssssssso.",
	"     :osssssss/        osssso+++.",
	"    /ossssssss/        +ssssooo/-",
	"  `/ossssso+/:-        -:/+osssso+-",
	" `+sso+:-`                 `.-/+oso:",
	"`++:.                           `-/+/",
	".`                                 `/",
};
#define LOGO_ROWS (int)(sizeof(logo) / sizeof(logo[0]))

#define BLUE 0x1793d1 // Arch blue
#define WHITE 0xffffff
#define GREY 0xb0b0b0
#define DIM 0x808080
#define MAX_IDS 64

struct font {
	uint32_t width, height, stride, size, count;
	const uint8_t *glyphs;
	uint32_t map[128]; // ASCII code -> glyph
};

struct buffer {
	uint32_t fb, pitch;
	uint32_t *px;
};

struct screen {
	uint32_t *px;
	int w, h, stride; // stride in pixels
};

static int fail(const char *what)
{
	fprintf(stderr, "plinius-splash: %s: %s\n", what, strerror(errno));
	return -1;
}

static int xioctl(int fd, unsigned long req, void *arg)
{
	int r;
	do
		r = ioctl(fd, req, arg);
	while (r == -1 && (errno == EINTR || errno == EAGAIN));
	return r;
}

// load_font: reads a PSF1 or PSF2 console font. Its Unicode table, if any, says which glyph
// shows which character.
static int load_font(const char *path, struct font *f)
{
	static uint8_t data[1 << 20];
	FILE *fp = fopen(path, "rb");
	if (!fp)
		return -1;
	size_t n = fread(data, 1, sizeof(data), fp);
	fclose(fp);
	errno = EINVAL;
	uint32_t header, table;
	if (n >= 4 && data[0] == 0x36 && data[1] == 0x04) {
		// PSF1: magic, mode (1: 512 glyphs, 2 or 4: Unicode table), height; always 8 wide
		header = 4, table = data[2] & 6;
		f->count = data[2] & 1 ? 512 : 256, f->height = f->size = data[3], f->width = 8;
	} else {
		// PSF2: magic, version, header size, flags, glyph count, bytes per glyph, height, width
		uint32_t h[8];
		if (n < sizeof(h))
			return -1;
		memcpy(h, data, sizeof(h));
		if (h[0] != 0x864ab572)
			return -1;
		header = h[2], table = h[3] & 1;
		f->count = h[4], f->size = h[5], f->height = h[6], f->width = h[7];
	}
	f->stride = (f->width + 7) / 8;
	if (header > n || f->count == 0 || (uint64_t)f->count * f->size > n - header || f->width == 0 ||
	    f->stride * f->height > f->size)
		return -1;
	f->glyphs = data + header;
	for (uint32_t c = 0; c < 128; c++)
		f->map[c] = c < f->count ? c : 0;
	if (!table)
		return 0;
	// Unicode table: per glyph its characters, then sequences (ignored), then an end mark. PSF1
	// has 16-bit values (0xfffe, 0xffff), PSF2 UTF-8 (0xfe, 0xff), where bytes below 0x80 are
	// always ASCII characters.
	for (uint32_t c = 0; c < 128; c++)
		f->map[c] = UINT32_MAX;
	const uint8_t *p = f->glyphs + (uint64_t)f->count * f->size, *end = data + n;
	int wide = data[0] == 0x36, step = wide ? 2 : 1;
	uint32_t seq_mark = wide ? 0xfffe : 0xfe, end_mark = wide ? 0xffff : 0xff;
	for (uint32_t g = 0; g < f->count && p + step <= end; g++, p += step) {
		int seq = 0;
		for (; p + step <= end; p += step) {
			uint32_t v = wide ? (uint32_t)(p[0] | p[1] << 8) : p[0];
			if (v == end_mark)
				break;
			if (v == seq_mark)
				seq = 1;
			else if (!seq && v < 0x80 && f->map[v] == UINT32_MAX)
				f->map[v] = g;
		}
	}
	uint32_t unknown = f->map['?'] != UINT32_MAX ? f->map['?'] : 0;
	for (uint32_t c = 0; c < 128; c++)
		if (f->map[c] == UINT32_MAX)
			f->map[c] = unknown;
	return 0;
}

static void fill(const struct screen *s, int x, int y, int w, int h, uint32_t color)
{
	if (x < 0)
		w += x, x = 0;
	if (y < 0)
		h += y, y = 0;
	if (x + w > s->w)
		w = s->w - x;
	if (y + h > s->h)
		h = s->h - y;
	for (int j = 0; j < h; j++)
		for (int i = 0; i < w; i++)
			s->px[(y + j) * s->stride + x + i] = color;
}

// text: draws t with its top left corner at x, y, every font pixel as a scale x scale square
static void text(const struct screen *s, const struct font *f, int x, int y, int scale, const char *t,
		 uint32_t color)
{
	for (; *t; t++, x += f->width * scale) {
		unsigned char c = *t;
		const uint8_t *g = f->glyphs + (c < 128 ? f->map[c] : f->map['?']) * f->size;
		for (uint32_t row = 0; row < f->height; row++)
			for (uint32_t col = 0; col < f->width; col++)
				if (g[row * f->stride + col / 8] & (0x80 >> (col % 8)))
					fill(s, x + col * scale, y + row * scale, scale, scale, color);
	}
}

static void centered(const struct screen *s, const struct font *f, int y, int scale, const char *t,
		     uint32_t color)
{
	text(s, f, (s->w - (int)(strlen(t) * f->width * scale)) / 2, y, scale, t, color);
}

// status: "up H:MM:SS" and the last line of the log file without its time stamp
static void status(const char *path, char *out, size_t len)
{
	struct timespec ts;
	clock_gettime(CLOCK_BOOTTIME, &ts);
	long t = ts.tv_sec;
	int n = snprintf(out, len, "up %ld:%02ld:%02ld", t / 3600, t / 60 % 60, t % 60);
	char last[256] = "", line[256];
	FILE *fp = fopen(path, "r");
	if (fp) {
		while (fgets(line, sizeof(line), fp))
			if (line[0] != '\n')
				memcpy(last, line, sizeof(last));
		fclose(fp);
	}
	char *m = strchr(last, ' ');
	if (m && n > 0 && (size_t)n < len) {
		m[strcspn(m, "\n")] = 0;
		snprintf(out + n, len - n, " %s", m);
	}
}

// find_output: the first connected connector, its preferred mode and a CRTC that can drive it
static int find_output(int fd, uint32_t *conn, uint32_t *crtc, struct drm_mode_modeinfo *mode)
{
	uint32_t conns[MAX_IDS], crtcs[MAX_IDS], encs[MAX_IDS];
	struct drm_mode_card_res res = {0};
	if (xioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &res))
		return fail("resources");
	// The kernel copies at most the counts passed in and then reports the real ones
	uint32_t nconn = res.count_connectors < MAX_IDS ? res.count_connectors : MAX_IDS;
	uint32_t ncrtc = res.count_crtcs < MAX_IDS ? res.count_crtcs : MAX_IDS;
	res = (struct drm_mode_card_res){
		.connector_id_ptr = (uintptr_t)conns, .count_connectors = nconn,
		.crtc_id_ptr = (uintptr_t)crtcs, .count_crtcs = ncrtc,
		.encoder_id_ptr = (uintptr_t)encs, .count_encoders = 0,
	};
	if (xioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &res))
		return fail("resources");

	for (uint32_t i = 0; i < nconn; i++) {
		// The first call (no modes asked for) also probes the output
		struct drm_mode_get_connector c = {.connector_id = conns[i]};
		if (xioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &c) || c.connection != 1 || !c.count_modes)
			continue;
		struct drm_mode_modeinfo modes[MAX_IDS];
		uint32_t cencs[MAX_IDS];
		uint32_t nmodes = c.count_modes < MAX_IDS ? c.count_modes : MAX_IDS;
		uint32_t nencs = c.count_encoders < MAX_IDS ? c.count_encoders : MAX_IDS;
		c = (struct drm_mode_get_connector){
			.connector_id = conns[i],
			.modes_ptr = (uintptr_t)modes, .count_modes = nmodes,
			.encoders_ptr = (uintptr_t)cencs, .count_encoders = nencs,
		};
		// Copied only if the counts still fit
		if (xioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &c) || c.count_modes > nmodes || c.count_encoders > nencs)
			continue;
		*mode = modes[0];
		for (uint32_t m = 0; m < c.count_modes; m++)
			if (modes[m].type & DRM_MODE_TYPE_PREFERRED) {
				*mode = modes[m];
				break;
			}
		// The CRTC already lit (by the bootloader), else the first one an encoder can use
		*crtc = 0;
		if (c.encoder_id) {
			struct drm_mode_get_encoder e = {.encoder_id = c.encoder_id};
			if (!xioctl(fd, DRM_IOCTL_MODE_GETENCODER, &e))
				*crtc = e.crtc_id;
		}
		for (uint32_t j = 0; j < c.count_encoders && !*crtc; j++) {
			struct drm_mode_get_encoder e = {.encoder_id = cencs[j]};
			if (xioctl(fd, DRM_IOCTL_MODE_GETENCODER, &e))
				continue;
			for (uint32_t k = 0; k < ncrtc && !*crtc; k++)
				if (e.possible_crtcs & (1u << k))
					*crtc = crtcs[k];
		}
		if (*crtc) {
			*conn = conns[i];
			return 0;
		}
	}
	errno = ENODEV;
	return fail("no connected display");
}

static int create_buffer(int fd, uint32_t w, uint32_t h, struct buffer *b)
{
	struct drm_mode_create_dumb cd = {.height = h, .width = w, .bpp = 32};
	if (xioctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &cd))
		return fail("dumb buffer");
	struct drm_mode_fb_cmd fb = {.width = w, .height = h, .pitch = cd.pitch, .bpp = 32, .depth = 24,
				     .handle = cd.handle};
	if (xioctl(fd, DRM_IOCTL_MODE_ADDFB, &fb))
		return fail("framebuffer");
	struct drm_mode_map_dumb md = {.handle = cd.handle};
	if (xioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &md))
		return fail("map");
	void *p = mmap(NULL, cd.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, md.offset);
	if (p == MAP_FAILED)
		return fail("mmap");
	b->fb = fb.fb_id, b->pitch = cd.pitch, b->px = p;
	return 0;
}

int main(int argc, char **argv)
{
	if (argc < 4) {
		fprintf(stderr, "usage: plinius-splash FONT STATUS TITLE [LINE...]\n");
		return 2;
	}
	struct font f;
	if (load_font(argv[1], &f)) {
		fail(argv[1]);
		return 1;
	}
	int fd = open("/dev/dri/card0", O_RDWR | O_CLOEXEC);
	if (fd < 0) {
		fail("/dev/dri/card0");
		return 1;
	}
	uint32_t conn, crtc;
	struct drm_mode_modeinfo mode;
	if (find_output(fd, &conn, &crtc, &mode))
		return 1;
	int w = mode.hdisplay, h = mode.vdisplay;
	// The logo in the accent colour of the phone's desktop (plinius-boot passes it on), else Arch blue
	uint32_t logo_color = BLUE;
	const char *accent = getenv("PLINIUS_SPLASH_COLOR");
	if (accent && strlen(accent) == 6 && strspn(accent, "0123456789abcdefABCDEF") == 6)
		logo_color = strtoul(accent, NULL, 16);
	// Two buffers: each second the next picture goes into the one not shown, then they swap
	struct buffer buf[2];
	if (create_buffer(fd, w, h, &buf[0]) || create_buffer(fd, w, h, &buf[1]))
		return 1;

	// Layout in rows of the font at scale s: logo, gap, title (double size), gap, lines, status
	int cols = 0, lines = argc - 4;
	for (int r = 0; r < LOGO_ROWS; r++)
		if ((int)strlen(logo[r]) > cols)
			cols = strlen(logo[r]);
	int logo_cols = cols;
	if (2 * (int)strlen(argv[3]) > cols)
		cols = 2 * strlen(argv[3]);
	for (int i = 4; i < argc; i++)
		if ((int)strlen(argv[i]) > cols)
			cols = strlen(argv[i]);
	int rows = LOGO_ROWS + 1 + 2 + 1 + lines + 1;
	int s = w * 9 / 10 / (cols * (int)f.width), sh = h * 85 / 100 / (rows * (int)f.height);
	if (sh < s)
		s = sh;
	if (s < 1)
		s = 1;
	int cw = f.width * s, ch = f.height * s;
	int y0 = (h - rows * ch) / 2 > 0 ? (h - rows * ch) / 2 : 0;
	fprintf(stderr, "plinius-splash: %dx%d, scale %d\n", w, h, s);

	for (int frame = 0, shown = 0;; frame ^= 1) {
		const struct buffer *b = &buf[frame];
		const struct screen sc = {b->px, w, h, b->pitch / 4};
		fill(&sc, 0, 0, w, h, 0);
		int y = y0, x = (w - logo_cols * cw) / 2;
		for (int r = 0; r < LOGO_ROWS; r++, y += ch)
			text(&sc, &f, x, y, s, logo[r], logo_color);
		y += ch;
		centered(&sc, &f, y, 2 * s, argv[3], WHITE);
		y += 3 * ch;
		for (int i = 4; i < argc; i++, y += ch)
			centered(&sc, &f, y, s, argv[i], GREY);
		char st[512];
		status(argv[2], st, sizeof(st));
		if ((int)strlen(st) > w / cw)
			st[w / cw] = 0;
		centered(&sc, &f, y, s, st, DIM);

		struct drm_mode_crtc set = {.set_connectors_ptr = (uintptr_t)&conn, .count_connectors = 1,
					    .crtc_id = crtc, .fb_id = b->fb, .mode = mode, .mode_valid = 1};
		if (xioctl(fd, DRM_IOCTL_MODE_SETCRTC, &set)) {
			// Fatal only for the first picture; later the display may just be busy for a moment
			if (!shown) {
				fail("modeset");
				return 1;
			}
		} else if (!shown) {
			fprintf(stderr, "plinius-splash: picture shown\n");
			shown = 1;
		}
		sleep(1);
	}
}

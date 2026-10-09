#ifndef PS2_UI_H
#define PS2_UI_H

#include <vulkan/vulkan.h>

#include "ps2_gamepad.h"
#include "ps2_window.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    void            *hwnd;
    VkInstance       instance;
    VkPhysicalDevice phys;
    VkDevice         device;
    uint32_t         queue_family;
    VkQueue          queue;
    VkFormat         color_format;
    uint32_t         image_count;
    float            max_anisotropy;
} ps2_ui_init_info;

int  ps2_ui_init(const ps2_ui_init_info *info);
void ps2_ui_shutdown(void);
/* Window-layer events; 1 = consumed, the game must not act on it. */
int  ps2_ui_key_event(const ps2_win_event *e);
/* Gamepad events; 1 = consumed by controller rebinding. */
int  ps2_ui_pad_event(const ps2_pad_event *e);
int  ps2_ui_visible(void);
void ps2_ui_set_visible(int on);
int  ps2_ui_blocks_game_input(void);
void ps2_ui_draw(VkCommandBuffer cmd);
void ps2_ui_swapchain_changed(uint32_t image_count);

typedef struct {
    uint32_t scale;
    uint32_t guest_w, guest_h;
    uint32_t targets;
    uint64_t bytes_at_1x;
    uint32_t max_scale;
    uint32_t auto_scale;
    uint32_t failed_scale;
    float    max_line_width;
} ps2_render_info;

void ps2_video_render_info(ps2_render_info *out);

#ifdef __cplusplus
}
#endif

#endif

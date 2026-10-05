/* Guest addresses the runtime uses, written once with their US (SLUS-20851)
   values. Include this file with PS2_ADDR defined; it has no include guard.

   PS2_ADDR(name, US address, kind, words to verify)
   kind: FUNC  function entry (hook target)
         RET   return address recorded as an emitter site (jal/jalr + 8)
         CODE  other address inside .text
         END   exclusive end of a code or data range
         DATA  address outside .text (.data/.rodata/.sdata/.bss/.vutext)
   words: number of 32-bit words at the address that must hold the same
          values as in the executable (0..8). Checked at startup for
          FUNC/RET/CODE; for DATA only when the tables are generated.

   Names are unique C identifiers without the PS2_ prefix. The same address
   may appear under two names only with different kinds. Addresses are
   written as 8 hex digits (0x00XXXXXXu) so tools/regionmap still sees them
   as US anchors. */

/* Render taps: draw-buffer and ordering-table functions */
PS2_ADDR(RN_DB_OPEN,                0x00320098u, FUNC, 4)
PS2_ADDR(RN_DB_OPEN_END,            0x003200D4u, END,  0)
PS2_ADDR(RN_DB_CLOSE,               0x003200D8u, FUNC, 4)
PS2_ADDR(RN_DB_CLOSE_END,           0x0032016Cu, END,  0)
PS2_ADDR(RN_OT_INIT,                0x00320390u, FUNC, 4)
PS2_ADDR(RN_OT_INIT_END,            0x00320424u, END,  0)
PS2_ADDR(RN_OT_OPEN,                0x00320538u, FUNC, 4)
PS2_ADDR(RN_OT_OPEN_END,            0x003205CCu, END,  0)
PS2_ADDR(RN_OT_CLOSE,               0x003205D0u, FUNC, 4)
PS2_ADDR(RN_OT_CLOSE_END,           0x00320644u, END,  0)
PS2_ADDR(RN_OT_LINK,                0x00320648u, FUNC, 4)
PS2_ADDR(RN_OT_LINK_END,            0x00320694u, END,  0)
PS2_ADDR(RN_DC_FLUSH,               0x003265E8u, FUNC, 4)
PS2_ADDR(RN_DC_FLUSH_END,           0x003274FCu, END,  0)
PS2_ADDR(AC5_SCENE_DISPATCH,        0x0031CF00u, FUNC, 0)
PS2_ADDR(AC5_DRAWCTRL_PTR,          0x004459A8u, DATA, 0)
PS2_ADDR(AC5_SCENE_ROOT_PTR,        0x004459ACu, DATA, 0)

/* Render taps: 2D primitive writers */
PS2_ADDR(RN_2D_32B3F0,              0x0032B3F0u, FUNC, 4)
PS2_ADDR(RN_2D_32B3F0_END,          0x0032B5B0u, END,  0)
PS2_ADDR(RN_2D_32B5B0,              0x0032B5B0u, FUNC, 4)
PS2_ADDR(RN_2D_32B5B0_END,          0x0032B650u, END,  0)
PS2_ADDR(RN_2D_32B650,              0x0032B650u, FUNC, 4)
PS2_ADDR(RN_2D_32B650_END,          0x0032B6F0u, END,  0)
PS2_ADDR(RN_2D_32B6F0,              0x0032B6F0u, FUNC, 4)
PS2_ADDR(RN_2D_32B6F0_END,          0x0032B850u, END,  0)
PS2_ADDR(RN_2D_32B850,              0x0032B850u, FUNC, 4)
PS2_ADDR(RN_2D_32B850_END,          0x0032B924u, END,  0)
PS2_ADDR(RN_2D_32B928,              0x0032B928u, FUNC, 4)
PS2_ADDR(RN_2D_32B928_END,          0x0032B9B8u, END,  0)

/* Sun and lens-flare writers (also screen-pass ranges) */
PS2_ADDR(RN_SUN_CHAIN,              0x0011AAF0u, FUNC, 4)
PS2_ADDR(RN_SUN_CHAIN_END,          0x0011B82Cu, END,  0)
PS2_ADDR(RN_SUN_FLARE,              0x00118BC8u, FUNC, 4)
PS2_ADDR(RN_SUN_FLARE_END,          0x00119B74u, END,  0)
PS2_ADDR(RN_SUN_GLARE,              0x001B0680u, FUNC, 4)
PS2_ADDR(RN_SUN_GLARE_END,          0x001B09ECu, END,  0)

/* Render intents: primitive writer and 2D helpers */
PS2_ADDR(RN_PRIM_WRITER,            0x0031FB88u, FUNC, 4)
PS2_ADDR(RN_PRIM_WRITER_END,        0x0031FF00u, END,  0)
PS2_ADDR(RN_RECT_HELPER,            0x0031FF00u, FUNC, 4)
PS2_ADDR(RN_RECT_HELPER_END,        0x0031FFB0u, END,  0)
PS2_ADDR(RN_LINE_HELPER,            0x0031FFB0u, FUNC, 4)
PS2_ADDR(RN_LINE_HELPER_END,        0x00320050u, END,  0)

/* Render intents: group writers (the word pair identifies the function) */
PS2_ADDR(RN_GROUP_2D6E00,           0x002D6E00u, FUNC, 2)
PS2_ADDR(RN_GROUP_134598,           0x00134598u, FUNC, 2)
PS2_ADDR(RN_GROUP_134EB0,           0x00134EB0u, FUNC, 2)
PS2_ADDR(RN_GROUP_141AC8,           0x00141AC8u, FUNC, 2)
PS2_ADDR(RN_GROUP_141EC8,           0x00141EC8u, FUNC, 2)
PS2_ADDR(RN_GROUP_135378,           0x00135378u, FUNC, 2)
PS2_ADDR(RN_GROUP_1354D0,           0x001354D0u, FUNC, 2)
PS2_ADDR(RN_GROUP_135928,           0x00135928u, FUNC, 2)
PS2_ADDR(RN_GROUP_135EC0,           0x00135EC0u, FUNC, 2)
PS2_ADDR(RN_GROUP_13E300,           0x0013E300u, FUNC, 2)
PS2_ADDR(RN_GROUP_136330,           0x00136330u, FUNC, 2)
PS2_ADDR(RN_GROUP_136688,           0x00136688u, FUNC, 2)
PS2_ADDR(RN_GROUP_136A68,           0x00136A68u, FUNC, 2)
PS2_ADDR(RN_GROUP_136E88,           0x00136E88u, FUNC, 2)
PS2_ADDR(RN_GROUP_137010,           0x00137010u, FUNC, 2)
PS2_ADDR(RN_GROUP_137258,           0x00137258u, FUNC, 2)
PS2_ADDR(RN_GROUP_137C10,           0x00137C10u, FUNC, 2)
PS2_ADDR(RN_GROUP_138A58,           0x00138A58u, FUNC, 2)
PS2_ADDR(RN_GROUP_13E920,           0x0013E920u, FUNC, 2)
PS2_ADDR(RN_GROUP_13FB70,           0x0013FB70u, FUNC, 2)
PS2_ADDR(RN_GROUP_140780,           0x00140780u, FUNC, 2)
PS2_ADDR(RN_GROUP_13F660,           0x0013F660u, FUNC, 2)
PS2_ADDR(RN_GROUP_13F878,           0x0013F878u, FUNC, 2)
PS2_ADDR(RN_GROUP_140A20,           0x00140A20u, FUNC, 2)
PS2_ADDR(RN_GROUP_1410D0,           0x001410D0u, FUNC, 2)
PS2_ADDR(RN_GROUP_141478,           0x00141478u, FUNC, 2)
PS2_ADDR(RN_GROUP_149298,           0x00149298u, FUNC, 2)
PS2_ADDR(RN_GROUP_139250,           0x00139250u, FUNC, 2)
PS2_ADDR(RN_GROUP_13E6F8,           0x0013E6F8u, FUNC, 2)
PS2_ADDR(RN_GROUP_13DBD8,           0x0013DBD8u, FUNC, 2)
PS2_ADDR(RN_GROUP_13DE38,           0x0013DE38u, FUNC, 2)
PS2_ADDR(RN_GROUP_142A38,           0x00142A38u, FUNC, 2)

/* Render intents: sky dome and haze (haze compares words 0 and 2) */
PS2_ADDR(RN_SKY_DOME,               0x00114F20u, FUNC, 2)
PS2_ADDR(RN_SKY_HAZE,               0x00114A70u, FUNC, 3)
PS2_ADDR(RN_SKY_SEGS,               0x003C8370u, DATA, 0)
PS2_ADDR(RN_SKY_RINGTAB,            0x003C8A00u, DATA, 0)

/* Render intents: triangle and cloud functions */
PS2_ADDR(RN_CLIP_TRI,               0x001AF720u, FUNC, 2)
PS2_ADDR(RN_DRAW_FAN,               0x001AFE80u, FUNC, 2)
PS2_ADDR(RN_CLOUD_PROJECT,          0x001CF718u, FUNC, 2)
PS2_ADDR(RN_SPRITE_ROWS,            0x001CD568u, FUNC, 2)
PS2_ADDR(RN_CLOUD_FIELD,            0x001CFAF8u, FUNC, 0)
PS2_ADDR(RN_CLOUD_PLANES,           0x001D1D88u, FUNC, 2)
PS2_ADDR(RN_CLOUD_VERTEX,           0x001D0D30u, FUNC, 2)

/* Screen passes: emitter return sites and code ranges */
PS2_ADDR(RN_RET_16171C,             0x0016171Cu, RET,  0)
PS2_ADDR(RN_RET_1609A8,             0x001609A8u, RET,  0)
PS2_ADDR(RN_RET_1D059C,             0x001D059Cu, RET,  0)
PS2_ADDR(RN_RET_1D03E0,             0x001D03E0u, RET,  0)
PS2_ADDR(RN_RET_1CD2CC,             0x001CD2CCu, RET,  0)
PS2_ADDR(RN_RET_12CD88,             0x0012CD88u, RET,  0)
PS2_ADDR(RN_RET_2F0628,             0x002F0628u, RET,  0)
PS2_ADDR(RN_SKY_PASS,               0x00113EC8u, FUNC, 0)
PS2_ADDR(RN_SKY_PASS_END,           0x00114180u, END,  0)
PS2_ADDR(RN_SKY_PASS2,              0x00114580u, FUNC, 0)
PS2_ADDR(RN_SKY_PASS2_END,          0x0011467Cu, END,  0)
PS2_ADDR(RN_EFFECTS,                0x00205E28u, FUNC, 0)
PS2_ADDR(RN_EFFECTS_END,            0x0026BDCCu, END,  0)

/* Front-end 2D detection: code range and return sites */
PS2_ADDR(RN_FE_CODE,                0x002886D8u, FUNC, 0)
PS2_ADDR(RN_FE_CODE_END,            0x002EA730u, END,  0)
PS2_ADDR(RN_RET_12BF1C,             0x0012BF1Cu, RET,  0)
PS2_ADDR(RN_RET_12BFA8,             0x0012BFA8u, RET,  0)
PS2_ADDR(RN_RET_161150,             0x00161150u, RET,  0)

/* Vertex program microcode ranges (.vutext) */
PS2_ADDR(RN_VP_39DB10,              0x0039DB10u, DATA, 0)
PS2_ADDR(RN_VP_39DB10_END,          0x0039FEA0u, END,  0)
PS2_ADDR(RN_VP_3A0080,              0x003A0080u, DATA, 0)
PS2_ADDR(RN_VP_3A0080_END,          0x003A1100u, END,  0)
PS2_ADDR(RN_VP_3A1110,              0x003A1110u, DATA, 0)
PS2_ADDR(RN_VP_3A1110_END,          0x003A1D30u, END,  0)
PS2_ADDR(RN_VP_3A1D40,              0x003A1D40u, DATA, 0)
PS2_ADDR(RN_VP_3A1D40_END,          0x003A40B0u, END,  0)
PS2_ADDR(RN_VP_3A40C0,              0x003A40C0u, DATA, 0)
PS2_ADDR(RN_VP_3A40C0_END,          0x003A6600u, END,  0)
PS2_ADDR(RN_VP_3A6610,              0x003A6610u, DATA, 0)
PS2_ADDR(RN_VP_3A6610_END,          0x003A91B0u, END,  0)
PS2_ADDR(RN_VP_3A9350,              0x003A9350u, DATA, 0)
PS2_ADDR(RN_VP_3A9350_END,          0x003AA3B0u, END,  0)
PS2_ADDR(RN_VP_3AA410,              0x003AA410u, DATA, 0)
PS2_ADDR(RN_VP_3AA410_END,          0x003ACE40u, END,  0)
PS2_ADDR(RN_VP_3ACFE0,              0x003ACFE0u, DATA, 0)
PS2_ADDR(RN_VP_3ACFE0_END,          0x003AE100u, END,  0)
PS2_ADDR(RN_VP_3AE110,              0x003AE110u, DATA, 0)
PS2_ADDR(RN_VP_3AE110_END,          0x003B0D50u, END,  0)
PS2_ADDR(RN_VP_3B4080,              0x003B4080u, DATA, 0)
PS2_ADDR(RN_VP_3B4080_END,          0x003B7830u, END,  0)
PS2_ADDR(RN_VP_3BABD0,              0x003BABD0u, DATA, 0)
PS2_ADDR(RN_VP_3BABD0_END,          0x003BC220u, END,  0)
PS2_ADDR(RN_VP_3BC600,              0x003BC600u, DATA, 0)
PS2_ADDR(RN_VP_3BC600_END,          0x003BD980u, END,  0)
PS2_ADDR(RN_VP_3BE3E0,              0x003BE3E0u, DATA, 0)
PS2_ADDR(RN_VP_3BE3E0_END,          0x003BEF00u, END,  0)
PS2_ADDR(RN_VP_3BEF10,              0x003BEF10u, DATA, 0)
PS2_ADDR(RN_VP_3BEF10_END,          0x003C0650u, END,  0)
PS2_ADDR(RN_VP_3C06F0,              0x003C06F0u, DATA, 0)
PS2_ADDR(RN_VP_3C06F0_END,          0x003C2230u, END,  0)
PS2_ADDR(RN_VP_3C2240,              0x003C2240u, DATA, 0)
PS2_ADDR(RN_VP_3C2240_END,          0x003C3D70u, END,  0)
PS2_ADDR(RN_VP_3C3D80,              0x003C3D80u, DATA, 0)
PS2_ADDR(RN_VP_3C3D80_END,          0x003C5210u, END,  0)
PS2_ADDR(RN_VP_3C5220,              0x003C5220u, DATA, 0)
PS2_ADDR(RN_VP_3C5220_END,          0x003C6ED0u, END,  0)

/* Mods, hooks and patching */
PS2_ADDR(AC5_ULZ_SETUP,             0x00102A78u, FUNC, 4)
PS2_ADDR(AC5_HOOK_SELFTEST,         0x0011DB30u, FUNC, 0)
PS2_ADDR(AC5_CODE_END,              0x003992C0u, END,  0)

/* Widescreen patch words (written by the runtime, so checked at generate time only) */
PS2_ADDR(AC5_WS_0,                  0x00440828u, DATA, 1)
PS2_ADDR(AC5_WS_1,                  0x0044082Cu, DATA, 1)

/* Parameter getters */
PS2_ADDR(AC5_PARAM_S8,              0x00324568u, FUNC, 2)
PS2_ADDR(AC5_PARAM_U8,              0x00324688u, FUNC, 2)
PS2_ADDR(AC5_PARAM_S16,             0x003247B0u, FUNC, 2)
PS2_ADDR(AC5_PARAM_U16,             0x00324910u, FUNC, 2)
PS2_ADDR(AC5_PARAM_S32,             0x00324A68u, FUNC, 2)
PS2_ADDR(AC5_PARAM_INT,             0x00324BC0u, FUNC, 2)
PS2_ADDR(AC5_PARAM_FLOAT,           0x00324D10u, FUNC, 2)
PS2_ADDR(AC5_PARAM_STRING,          0x00324E68u, FUNC, 2)

/* Game objects reached through gp (separate entries: .sdata objects and gp moved by different amounts in JP) */
PS2_ADDR(AC5_APP_PTR,               0x004432ACu, DATA, 0)
PS2_ADDR(AC5_DISC_QUEUE_PTR,        0x00444000u, DATA, 0)
PS2_ADDR(AC5_SND_CHANNELS_PTR,      0x00445ABCu, DATA, 0)

/* Streamed-sound sequence counter */
PS2_ADDR(AC5_NUSNDSTR_SEQ,          0x0047EC9Cu, DATA, 0)

/**
 * Servo ranges — single source of truth for DXL bounds, mirrored from
 * packages/animator/.../pose.py CLAMP. Imported by drag math, the pet page,
 * and tests so they all agree on the safe envelope.
 */
export const SERVO_RANGES = {
  head_lr: [1828, 2298] as const,
  head_ud: [2885, 3278] as const,
  eye:     [1995, 2085] as const,
  jaw:     [1594, 1811] as const,
  brow:    [2056, 2087] as const,
};

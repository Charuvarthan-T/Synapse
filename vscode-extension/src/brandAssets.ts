/** Shared inline brain mark, recolorable (the OS 🧠 emoji can't be themed). */
export function brainSvg(size: number, color: string): string {
  return `<svg width="${size}" height="${size}" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg">
    <path fill="${color}" d="
      M6.1 1.2c-1 .1-1.8.8-2 1.7-.9.1-1.6.8-1.7 1.7-.7.3-1.2 1-1.2 1.8
      0 .5.2 1 .5 1.3-.3.4-.5.9-.5 1.4 0 .8.5 1.5 1.1 1.8
      -.1.9.4 1.7 1.2 2.1.1.9.9 1.6 1.8 1.6.5 0 1-.2 1.3-.5
      V2.9c0-.9-.7-1.7-1.6-1.7h-.9z
      M9.9 1.2c1 .1 1.8.8 2 1.7.9.1 1.6.8 1.7 1.7.7.3 1.2 1 1.2 1.8
      0 .5-.2 1-.5 1.3.3.4.5.9.5 1.4 0 .8-.5 1.5-1.1 1.8
      .1.9-.4 1.7-1.2 2.1-.1.9-.9 1.6-1.8 1.6-.5 0-1-.2-1.3-.5
      V2.9c0-.9.7-1.7 1.6-1.7h.9z"/>
  </svg>`;
}

## How it works

CHROMA is a single chip that both **plays music** and **draws a music player's user interface**.

**Graphics.** The chip outputs 640x480 @ 60 Hz VGA with no framebuffer. Each pixel's color is computed on the fly as the beam scans ("racing the beam"). It draws these layers, front to back:

- the title "CHROMA" in a 5x7 font scaled 4x, with a shine that sweeps across in time with the song
- a 16-band, 16-segment LED-style visualizer. The tallest bar sits at a position set by the current note's pitch, and the shape shrinks as the volume envelope decays.
- a progress bar with a playhead that advances every frame
- play/pause and next-track icons, plus four track-indicator dots
- a 4x4 ordered-dither background gradient, which gives 16 shades from one bit of color

**Audio.** A 32-step sequencer steps through an original melody at 7.5 steps per second. Each note retriggers a 4-bit decaying envelope. A square-wave oscillator, driven by a 12-entry period table plus octave shifts, is gated by a ~1.5 MHz PWM whose duty cycle equals the envelope. That PWM signal is the 1-bit audio output on `uio[7]`.

**Tracks.** Four variations of the song: normal, an octave up, reversed, and reversed an octave up. Each has its own color theme.

## How to test

1. Connect a TinyVGA PMOD to the output pins and a Tiny Tapeout Audio PMOD to the bidirectional pins.
2. Set the clock to 25.175 MHz and reset.
3. The song starts playing. Hold `ui[0]` high to pause. Pulse `ui[1]` to skip to the next track.

## External hardware

- TinyVGA PMOD (VGA output)
- Tiny Tapeout Audio PMOD (or an RC low-pass filter on `uio[7]` into an amplifier)

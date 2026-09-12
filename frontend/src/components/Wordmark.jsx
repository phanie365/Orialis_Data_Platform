/**
 * The ORIALIS wordmark.
 *
 * Three parts, in the proportions used throughout the moodboard: the leaf
 * mark, the name set in a wide-tracked serif capital, and the descriptor
 * beneath it at roughly a third of the size, tracked wider still.
 *
 * The leaf is drawn inline as an SVG rather than loaded as an asset so it
 * inherits `currentColor` and works on both the forest rail and a light
 * ground without a second file.
 */

import './Wordmark.css'

export function Wordmark({ tone = 'light' }) {
  return (
    <div className={`wordmark wordmark--${tone}`}>
      <svg
        className="wordmark__mark"
        viewBox="0 0 28 34"
        aria-hidden="true"
        focusable="false"
      >
        {/* Two leaves sharing a stem: the filled one is the brand green,
            the open one is drawn in outline. Same construction as the
            moodboard mark, simplified to hold up at 22px. */}
        <path
          d="M14 33 C14 24 14 16 14 7"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.1"
          strokeLinecap="round"
          opacity="0.75"
        />
        <path
          d="M14 18 C14 9 18.5 2.5 25.5 0.8 C26.8 8.4 23 16.2 14 18 Z"
          fill="currentColor"
        />
        <path
          d="M13.2 22 C13.2 14.6 9.6 8.8 3.4 7.2 C2.2 13.6 5.6 20.4 13.2 22 Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.1"
          strokeLinejoin="round"
          opacity="0.62"
        />
      </svg>

      <div className="wordmark__text">
        <div className="wordmark__name">ORIALIS</div>
        <div className="wordmark__descriptor">Wealth Management</div>
      </div>
    </div>
  )
}

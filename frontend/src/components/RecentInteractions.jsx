/**
 * The activity feed: the eight most recent interactions.
 *
 * The one panel on the dashboard fed by an operational endpoint rather than
 * by the aggregates. `/api/v1/interactions` already sorts by
 * `interaction_date DESC, interaction_id DESC`, so the rows arrive in the
 * right order and the id tiebreaker keeps them stable between reloads.
 *
 * Every field displayed exists on the record: client, advisor, type,
 * channel, subject, outcome, date. Nothing is derived or inferred.
 */

import { formatTimestamp, outcomeTone } from '../format'
import './RecentInteractions.css'

export function RecentInteractions({ interactions }) {
  return (
    <div className="feed">
      <table className="feed__table">
        <thead>
          <tr>
            <th className="label">When</th>
            <th className="label">Client</th>
            <th className="label">Advisor</th>
            <th className="label">Subject</th>
            <th className="label">Channel</th>
            <th className="label">Outcome</th>
          </tr>
        </thead>
        <tbody>
          {interactions.map((interaction) => (
            <tr key={interaction.interaction_id}>
              <td className="feed__when tnum">
                {formatTimestamp(interaction.interaction_date)}
              </td>
              <td className="feed__id tnum">{interaction.client_id}</td>
              <td className="feed__id tnum">{interaction.advisor_id}</td>
              <td className="feed__subject">
                <span className="feed__subjectText">{interaction.subject}</span>
                <span className="feed__type">{interaction.interaction_type}</span>
              </td>
              <td className="feed__channel">{interaction.channel}</td>
              <td>
                <span className={`dot dot--${outcomeTone(interaction.outcome)}`} />
                <span className="feed__outcome">{interaction.outcome}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * Advisors ranked by the number of clients assigned to them.
 *
 * This is the panel that justified the `/stats/overview` endpoint: built
 * from the paginated API it would have cost one request per advisor.
 *
 * Only what the CRM actually holds is shown - identifier, name, and the
 * count. No revenue, no assets under management, no performance score: the
 * database contains none of those, and a wealth-management dashboard is
 * exactly the wrong place to invent them.
 */

import './TopAdvisors.css'

export function TopAdvisors({ advisors }) {
  const largest = Math.max(...advisors.map((advisor) => advisor.client_count), 1)

  return (
    <ol className="advisors">
      {advisors.map((advisor, index) => (
        <li className="advisors__row" key={advisor.advisor_id}>
          {/* The rank set in the serif, the way a figure is set in a printed
              report rather than in a coloured badge. */}
          <span className="advisors__rank">{index + 1}</span>

          <span className="advisors__identity">
            <span className="advisors__name">
              {advisor.first_name} {advisor.last_name}
            </span>
            <span className="advisors__id">{advisor.advisor_id}</span>
          </span>

          <span className="advisors__bar">
            <span
              className="advisors__fill"
              style={{ width: `${(advisor.client_count / largest) * 100}%` }}
            />
          </span>

          <span className="advisors__count tnum">
            {advisor.client_count.toLocaleString('en-GB')}
            <span className="advisors__unit">clients</span>
          </span>
        </li>
      ))}
    </ol>
  )
}

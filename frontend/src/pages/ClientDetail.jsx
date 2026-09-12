/**
 * One client, assembled from three existing endpoints.
 *
 *   /clients/{id}                       the record itself
 *   /advisors/{advisor_id}              the advisor it points to
 *   /interactions?client_id={id}         that client's recent history
 *
 * The advisor request is deliberately chained rather than parallel: the
 * advisor's id is inside the client record, so there is nothing to ask for
 * until the client has arrived. The interaction history does not depend on
 * anything and starts immediately, alongside the client.
 *
 * No wealth figures. The CRM stores no portfolio value, no assets under
 * management and no performance, so this page shows none - a plausible-looking
 * number in a wealth-management interface is worse than an absent one.
 */

import { fetchAdvisor, fetchClient, fetchInteractions } from '../api/client'
import { useApi } from '../api/useApi'
import { Shell } from '../components/Shell'
import { formatDate, formatTimestamp, outcomeTone } from '../format'
import { Link } from '../router'
import './ClientDetail.css'

/** One label / value pair on a hairline. */
function Fact({ label, children }) {
  return (
    <div className="fact">
      <dt className="label">{label}</dt>
      <dd className="fact__value">{children ?? '—'}</dd>
    </div>
  )
}

function Section({ title, note, children }) {
  return (
    <section className="panel detail__section">
      <div className="panel__head">
        <h2 className="panel__title">{title}</h2>
        {note && <span className="panel__note">{note}</span>}
      </div>
      <div className="panel__body">{children}</div>
    </section>
  )
}

export function ClientDetail({ clientId }) {
  const client = useApi((options) => fetchClient(clientId, options), [clientId])

  // Starts at the same time as the client: it needs only the id from the URL.
  const interactions = useApi(
    (options) => fetchInteractions({ client_id: clientId, page_size: 10 }, options),
    [clientId],
  )

  const advisorId = client.data?.advisor_id

  // Chained: there is nothing to fetch until the client names an advisor.
  // The fetcher resolves to null rather than throwing when the id is absent,
  // so the hook's loading state stays honest instead of reporting an error
  // for a request that was never made.
  const advisor = useApi(
    (options) => (advisorId ? fetchAdvisor(advisorId, options) : Promise.resolve(null)),
    [advisorId],
  )

  const record = client.data

  // -- Not found ----------------------------------------------------------
  // A 404 here is a real answer, not a failure: the id in the URL does not
  // exist. It deserves its own page rather than a red error box.
  if (client.error?.status === 404) {
    return (
      <Shell
        title="Client not found"
        breadcrumb={<Link to="/clients">Clients</Link>}
      >
        <div className="panel">
          <div className="state">
            No client exists with the identifier <strong>{clientId}</strong>.
            <div className="state__detail">
              <Link to="/clients">Return to the client list</Link>
            </div>
          </div>
        </div>
      </Shell>
    )
  }

  if (client.error) {
    return (
      <Shell title="Clients" breadcrumb={<Link to="/clients">Clients</Link>}>
        <div className="panel">
          <div className="state state--error">
            {client.error.message}
            {client.error.status === 401 && (
              <div className="state__detail">
                Set CRM_API_KEY in frontend/.env, then restart the dev server.
              </div>
            )}
          </div>
        </div>
      </Shell>
    )
  }

  const fullName = record ? `${record.first_name} ${record.last_name}` : '…'

  return (
    <Shell
      title={client.loading ? 'Loading…' : fullName}
      breadcrumb={
        <>
          <Link to="/clients">Clients</Link>
          <span className="detail__crumbSep">·</span>
          <span className="tnum">{clientId}</span>
        </>
      }
      actions={
        record && (
          <div className="detail__status">
            <span className="label">Status</span>
            <div className={`status status--${(record.client_status || '').toLowerCase()}`}>
              {record.client_status}
            </div>
          </div>
        )
      }
    >
      {client.loading ? (
        <div className="detail__grid">
          {Array.from({ length: 3 }, (_, index) => (
            <div className="panel detail__section" key={index}>
              <div className="panel__body">
                <span className="skeleton detail__skeleton" />
                <span className="skeleton detail__skeleton" />
                <span className="skeleton detail__skeleton" />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <>
          {/* -- Classification, as the three brand facts ------------------ */}
          <div className="detail__summary">
            <div className="summaryItem">
              <span className="label">Segment</span>
              <span className="summaryItem__value">{record.client_segment}</span>
            </div>
            <div className="summaryItem">
              <span className="label">Risk profile</span>
              <span className="summaryItem__value">{record.risk_profile}</span>
            </div>
            <div className="summaryItem">
              <span className="label">Client since</span>
              <span className="summaryItem__value tnum">
                {formatDate(record.created_at, { long: true })}
              </span>
            </div>
          </div>

          <div className="detail__grid">
            <Section title="Identity">
              <dl className="facts">
                <Fact label="Client id">
                  <span className="tnum">{record.client_id}</span>
                </Fact>
                <Fact label="First name">{record.first_name}</Fact>
                <Fact label="Last name">{record.last_name}</Fact>
                <Fact label="Date of birth">
                  <span className="tnum">{formatDate(record.birth_date, { long: true })}</span>
                </Fact>
                <Fact label="Nationality">{record.nationality}</Fact>
                <Fact label="Preferred language">{record.preferred_language}</Fact>
              </dl>
            </Section>

            <Section title="Contact">
              <dl className="facts">
                <Fact label="Email">
                  <a className="fact__link" href={`mailto:${record.email}`}>
                    {record.email}
                  </a>
                </Fact>
                <Fact label="Phone">
                  <span className="tnum">{record.phone}</span>
                </Fact>
                <Fact label="City of residence">{record.city_of_residence}</Fact>
                <Fact label="Country of residence">{record.country_of_residence}</Fact>
                <Fact label="Last updated">
                  <span className="tnum">{formatTimestamp(record.updated_at)}</span>
                </Fact>
              </dl>
            </Section>

            <Section title="Advisor">
              {advisor.loading && <div className="state">Loading…</div>}

              {!advisor.loading && advisor.error && (
                <div className="state state--error">{advisor.error.message}</div>
              )}

              {!advisor.loading && !advisor.error && advisor.data && (
                <>
                  <div className="advisorCard">
                    <div className="advisorCard__name">
                      {advisor.data.first_name} {advisor.data.last_name}
                    </div>
                    <div className="advisorCard__title">{advisor.data.job_title}</div>
                  </div>

                  <dl className="facts">
                    <Fact label="Advisor id">
                      <span className="tnum">{advisor.data.advisor_id}</span>
                    </Fact>
                    <Fact label="Specialization">{advisor.data.specialization}</Fact>
                    <Fact label="Languages">
                      {/* Split, as on the advisor list: the column stores
                          "FR,EN,IT" and the raw string reads as one token. */}
                      <span className="advisorLangs">
                        {(advisor.data.spoken_languages || '')
                          .split(',')
                          .map((language) => language.trim())
                          .filter(Boolean)
                          .map((language) => (
                            <span className="advisorLangs__item" key={language}>
                              {language}
                            </span>
                          ))}
                      </span>
                    </Fact>
                    <Fact label="Email">
                      <a className="fact__link" href={`mailto:${advisor.data.email}`}>
                        {advisor.data.email}
                      </a>
                    </Fact>
                    <Fact label="Branch">
                      <Link
                        className="fact__link"
                        to={`/advisors?branch_id=${advisor.data.branch_id}`}
                      >
                        {advisor.data.branch_id}
                      </Link>
                    </Fact>
                  </dl>
                </>
              )}

              {!advisor.loading && !advisor.error && !advisor.data && (
                <div className="state">No advisor is assigned to this client.</div>
              )}
            </Section>
          </div>

          {/* -- History ---------------------------------------------------- */}
          <Section
            title="Recent Interactions"
            note={
              interactions.data
                ? `${interactions.data.pagination.total_records.toLocaleString('en-GB')} recorded`
                : undefined
            }
          >
            {interactions.loading && <div className="state">Loading…</div>}

            {!interactions.loading && interactions.error && (
              <div className="state state--error">{interactions.error.message}</div>
            )}

            {!interactions.loading &&
              !interactions.error &&
              interactions.data?.data.length === 0 && (
                <div className="state">No interaction has been recorded for this client.</div>
              )}

            {!interactions.loading &&
              !interactions.error &&
              interactions.data?.data.length > 0 && (
                <>
                  <ol className="history">
                    {interactions.data.data.map((interaction) => (
                      <li className="history__row" key={interaction.interaction_id}>
                        <div className="history__when tnum">
                          {formatTimestamp(interaction.interaction_date)}
                        </div>
                        <div className="history__body">
                          <div className="history__subject">{interaction.subject}</div>
                          <div className="history__meta">
                            {interaction.interaction_type} · {interaction.channel}
                          </div>
                        </div>
                        <div className="history__outcome">
                          <span className={`dot dot--${outcomeTone(interaction.outcome)}`} />
                          {interaction.outcome}
                        </div>
                      </li>
                    ))}
                  </ol>

                  {interactions.data.pagination.total_records >
                    interactions.data.data.length && (
                    <div className="history__more">
                      <Link to={`/interactions?client_id=${clientId}`}>
                        View the full history
                      </Link>
                    </div>
                  )}
                </>
              )}
          </Section>
        </>
      )}
    </Shell>
  )
}

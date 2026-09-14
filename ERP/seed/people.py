"""
The humans in the ERP, and the friction that makes reconciliation real.

---------------------------------------------------------------------------
WHY A LIST OF NAMES SITS IN THE ERP SEED
---------------------------------------------------------------------------

`SHARED_ADVISORS` below holds 93 people who also exist in the CRM. It is a
SNAPSHOT, captured once at authoring time by reading the CRM's public API, and
frozen here as data.

This does not couple the two systems, and the distinction matters:

    - no import, no foreign key, no connection - the ERP never reads the CRM
      at runtime, and nothing in the schema references it
    - no identifier is shared. `ADV012` appears nowhere. What is shared is a
      NAME, because it is the same human being: Sophie Vidal works for
      Orialis, so payroll knows her and so does the CRM
    - no mapping table exists, deliberately. Matching the two registers is a
      platform problem, and this file is careful not to solve it in advance

That is exactly the real situation. Two systems hold overlapping populations
because the populations are the same people, not because the systems talk.

---------------------------------------------------------------------------
THE FRICTION, AND WHY IT IS DELIBERATE
---------------------------------------------------------------------------

A reconciliation exercise is only an exercise if the data resists. Six
frictions are built in on purpose:

    93   people in both systems                     the matchable core
      7   CRM advisors with NO ERP employee          left, or paid elsewhere
      5   ERP advisors with NO CRM advisor           too recent for the CRM
     73   support staff                              no CRM existence at all
     25   spelling variants among the 93             accents, case, particles
     ~5%  of employees with no work_email            no email join possible

The variants are the interesting part. The CRM writes names without accents;
a payroll system keeps the civil-status spelling. So "Melanie Morel" in the
CRM is "Mélanie Morel" here, "Bertrand" is "BERTRAND", and the Belgian
"Vandenberg" is "Van den Berg". None of it is wrong on either side - it is
what two systems maintained by two teams actually look like, and it is what
stops a naive equijoin on the full name from working.

Office codes are the same idea applied to places: this file knows FR-PAR-01,
never BR001, and no rule derives one from the other.
"""

# ---------------------------------------------------------------------------
# The 93 shared people
# ---------------------------------------------------------------------------
# (first_name, last_name, office_code, hire_date)
#
# `hire_date` comes from the CRM snapshot too: the same person was hired on
# the same day in both systems, which is one of the few attributes a
# reconciliation can lean on when the names disagree.

SHARED_ADVISORS = [
    ("Sophie", "BERTRAND", "FR-PAR-01", "2015-04-20"),
    ("Lucie", "Bonnet", "FR-PAR-01", "2016-03-11"),
    ("Justine", "Noël", "FR-PAR-01", "2016-09-21"),
    ("Adrien", "Leroy", "FR-PAR-01", "2024-07-20"),
    ("Adrien", "Laurent", "FR-PAR-01", "2013-01-02"),
    ("Marion", "Masson", "FR-PAR-01", "2015-02-07"),
    ("Alice", "Dubois", "FR-PAR-01", "2025-01-01"),
    ("Thomas", "COLIN", "FR-PAR-01", "2022-09-20"),
    ("Mélanie", "Morel", "FR-PAR-01", "2016-12-27"),
    ("Justine", "Dubois", "FR-PAR-01", "2017-01-27"),
    ("Justine", "Bonnet", "FR-PAR-01", "2015-08-27"),
    ("Sophie", "Vidal", "FR-PAR-01", "2017-02-17"),
    ("Sophie", "MOREAU", "FR-PAR-01", "2024-12-02"),
    ("Thomas", "RENARD", "FR-PAR-01", "2018-10-08"),
    ("Alice", "Roux", "FR-PAR-01", "2014-12-02"),
    ("Hugo", "Faure", "FR-PAR-01", "2011-03-02"),
    ("Justine", "Laurent", "FR-PAR-01", "2019-06-24"),
    ("Lucie", "Morel", "FR-PAR-01", "2019-09-29"),
    ("Louis", "Noël", "FR-PAR-01", "2017-10-13"),
    ("Claire", "Bertrand", "FR-PAR-01", "2023-06-10"),
    ("Nicolas", "ROUX", "FR-PAR-01", "2017-04-09"),
    ("Romain", "Masson", "FR-PAR-01", "2013-04-03"),
    ("Xavier", "Vidal", "FR-PAR-01", "2015-07-15"),
    ("Julien", "Bertrand", "FR-PAR-01", "2019-06-19"),
    ("Claire", "LEROY", "FR-LYO-01", "2011-07-14"),
    ("Sophie", "Fontaine", "FR-LYO-01", "2013-06-19"),
    ("Quentin", "Roux", "FR-LYO-01", "2015-12-31"),
    ("Sophie", "Perrin", "FR-LYO-01", "2017-06-23"),
    ("Sophie", "BARBIER", "FR-LYO-01", "2015-04-01"),
    ("Romain", "Laurent", "FR-LYO-01", "2018-05-04"),
    ("Marc", "Garnier", "FR-LYO-01", "2012-01-05"),
    ("Xavier", "Garnier", "FR-LYO-01", "2017-08-06"),
    ("Mélanie", "Dubois", "FR-LYO-01", "2017-03-23"),
    ("Vincent", "ROUX", "FR-LYO-01", "2016-05-06"),
    ("Thomas", "Lefèvre", "FR-LYO-01", "2017-02-16"),
    ("Laurent", "Faure", "FR-LYO-01", "2018-08-17"),
    ("Xavier", "ROUX", "FR-LYO-01", "2023-03-21"),
    ("Julie", "Blanchard", "FR-LYO-01", "2019-07-05"),
    ("Pauline", "Garnier", "FR-LYO-01", "2015-11-03"),
    ("Antoine", "Colin", "FR-LYO-01", "2024-12-06"),
    ("Thibault", "Simons", "BE-BRU-01", "2014-01-21"),
    ("Kevin", "Dupuis", "BE-BRU-01", "2016-05-20"),
    ("Kevin", "Lambert", "BE-BRU-01", "2025-04-09"),
    ("Élise", "PEETERS", "BE-BRU-01", "2018-03-15"),
    ("Kevin", "GOOSSENS", "BE-BRU-01", "2018-05-31"),
    ("Bruno", "Wouters", "BE-BRU-01", "2016-11-28"),
    ("Élise", "VERHOEVEN", "BE-BRU-01", "2021-06-13"),
    ("Kevin", "Verhoeven", "BE-BRU-01", "2017-06-01"),
    ("Axelle", "Servais", "BE-BRU-01", "2022-12-30"),
    ("Gilles", "Van den Berg", "BE-BRU-01", "2018-02-27"),
    ("Charlotte", "Goossens", "BE-BRU-01", "2019-09-07"),
    ("Patrick", "Zbinden", "CH-GVA-01", "2015-07-28"),
    ("Laura", "Vionnet", "CH-GVA-01", "2019-10-11"),
    ("Yann", "Rochat", "CH-GVA-01", "2023-06-27"),
    ("Isabelle", "Terrier", "CH-GVA-01", "2021-08-07"),
    ("Fabrice", "Girod", "CH-GVA-01", "2018-05-21"),
    ("Guillaume", "Baumann", "CH-GVA-01", "2019-09-07"),
    ("Nathalie", "Terrier", "CH-GVA-01", "2023-01-26"),
    ("Yann", "Chappuis", "CH-GVA-01", "2020-11-05"),
    ("Ludovic", "Aebischer", "CH-GVA-01", "2016-05-21"),
    ("Grégoire", "Girod", "CH-GVA-01", "2016-07-07"),
    ("Isabelle", "Duperret", "CH-GVA-01", "2020-06-07"),
    ("Laura", "Jaquet", "CH-GVA-01", "2024-09-01"),
    ("Fabrice", "Terrier", "CH-GVA-01", "2017-11-06"),
    ("Carole", "MEYLAN", "CH-GVA-01", "2015-11-14"),
    ("Laura", "Duperret", "CH-GVA-01", "2020-05-24"),
    ("Mélissa", "Meylan", "CH-GVA-01", "2018-10-18"),
    ("Sébastien", "MONNIER", "CH-LAU-01", "2014-03-30"),
    ("Mélissa", "Perret", "CH-LAU-01", "2012-11-13"),
    ("Laura", "NICOLET", "CH-LAU-01", "2017-01-05"),
    ("Sandrine", "Pittet", "CH-LAU-01", "2021-11-15"),
    ("Michel", "Perret", "CH-LAU-01", "2020-08-17"),
    ("Patrick", "Rey", "CH-LAU-01", "2012-12-17"),
    ("Guillaume", "Berger", "CH-LAU-01", "2015-08-18"),
    ("Sylvie", "Baumann", "CH-LAU-01", "2019-07-13"),
    ("Martina", "Moretti", "IT-MIL-01", "2015-05-03"),
    ("Marco", "Esposito", "IT-MIL-01", "2022-04-25"),
    ("Giulia", "Marino", "IT-MIL-01", "2018-01-12"),
    ("Elena", "Lombardi", "IT-MIL-01", "2019-12-18"),
    ("Lorenzo", "Rizzo", "IT-MIL-01", "2013-06-27"),
    ("Marco", "VILLA", "IT-MIL-01", "2017-03-03"),
    ("Sara", "Russo", "IT-MIL-01", "2017-09-16"),
    ("Valentina", "Ricci", "IT-MIL-01", "2013-10-14"),
    ("Sara", "COLOMBO", "IT-MIL-01", "2017-07-20"),
    ("Giulia", "Moretti", "IT-MIL-01", "2019-06-26"),
    ("Martina", "Barbieri", "IT-MIL-01", "2020-06-29"),
    ("Beatrice", "Bianchi", "IT-ROM-01", "2011-06-21"),
    ("Riccardo", "BIANCHI", "IT-ROM-01", "2017-04-12"),
    ("Davide", "Marino", "IT-ROM-01", "2022-06-17"),
    ("Chiara", "Greco", "IT-ROM-01", "2019-09-29"),
    ("Valentina", "Bianchi", "IT-ROM-01", "2021-12-10"),
    ("Matteo", "Marino", "IT-ROM-01", "2024-03-28"),
    ("Beatrice", "RIZZO", "IT-ROM-01", "2016-12-12"),
]

# The 7 CRM advisors deliberately absent from the ERP, recorded here as
# documentation only - nothing reads this list. They are the other half of the
# friction: a reconciliation must cope with unmatched rows on BOTH sides.
CRM_ONLY_ADVISORS = [
    "Aurore Jacobs", "Melissa Blanc", "Sandrine Perret", "Sandrine Berger",
    "Laura Rey", "Beatrice Esposito", "Giulia Lombardi",
]

# The 5 advisors who exist in the ERP and NOT in the CRM: hired late in the
# window, already on payroll, not yet carrying a client portfolio.
ERP_ONLY_ADVISORS = [
    ("Océane", "Baudry", "FR-PAR-01"),
    ("Tanguy", "Delcourt", "BE-BRU-01"),
    ("Aurélien", "Pasche", "CH-GVA-01"),
    ("Federica", "Gallo", "IT-MIL-01"),
    ("Maël", "Kerviel", "FR-LYO-01"),
]


# ---------------------------------------------------------------------------
# Name pools for the ERP-only population
# ---------------------------------------------------------------------------
# Support staff, and the advisors who left during the window. None of these
# people exist in the CRM - back office, IT and compliance have no commercial
# relationship, and a departed advisor is removed from the CRM but kept on
# payroll for ever.
#
# Accented throughout, consistent with the civil-status spelling the ERP uses.

FIRST_NAMES = {
    "FR": ["Amélie", "Benoît", "Cédric", "Delphine", "Étienne", "Florence",
           "Gaëlle", "Hervé", "Inès", "Jérôme", "Karine", "Loïc", "Margaux",
           "Nadège", "Olivier", "Perrine", "Renaud", "Solène", "Tristan",
           "Valentin", "Yannick", "Zoé", "Anaïs", "Bastien", "Clémence",
           "Dorian", "Élodie", "Fabien", "Gauthier", "Héloïse"],
    "BE": ["Annelies", "Bart", "Céline", "Dries", "Emeline", "Frédéric",
           "Geert", "Hanne", "Ils", "Joris", "Katrien", "Lieven", "Margot",
           "Niels", "Ophélie", "Pieter", "Roxane", "Stijn", "Tine", "Wim"],
    "CH": ["Aline", "Béatrice", "Cyril", "Daniela", "Emmanuel", "Fanny",
           "Gérald", "Heidi", "Ivan", "Joëlle", "Kurt", "Lionel", "Muriel",
           "Noémie", "Olivier", "Pascale", "Raphaël", "Sonia", "Thierry",
           "Ursula"],
    "IT": ["Alessia", "Bruno", "Caterina", "Daniele", "Eleonora", "Fabio",
           "Gabriella", "Ignazio", "Lucia", "Massimo", "Nicoletta", "Paolo",
           "Rosaria", "Stefano", "Teresa", "Umberto", "Vittoria", "Alberto",
           "Benedetta", "Cristian"],
}

LAST_NAMES = {
    "FR": ["Aubert", "Béguin", "Charpentier", "Deschamps", "Estève",
           "Ferrand", "Gaillard", "Hébert", "Imbert", "Joubert", "Lacombe",
           "Maillard", "Navarro", "Ollivier", "Pénard", "Quéré", "Rivière",
           "Salomon", "Tessier", "Vacher", "Weber", "Bourdon", "Cazenave",
           "Delaunay", "Émery", "Faivre", "Grandjean", "Hamon", "Jullien",
           "Lascaux"],
    "BE": ["Adriaensen", "Boons", "Cools", "De Smet", "Everaert", "Fransen",
           "Geerts", "Huysmans", "Janssens", "Keppens", "Lemmens",
           "Mertens", "Nijs", "Ooms", "Pauwels", "Raes", "Segers",
           "Thijs", "Van Acker", "Wouters"],
    "CH": ["Aeschlimann", "Bovet", "Cornuz", "Dubath", "Eggenberger",
           "Favre", "Gremaud", "Hofmann", "Jordan", "Küng", "Liechti",
           "Marmy", "Nussbaum", "Oberson", "Piguet", "Rouiller", "Schaller",
           "Tinguely", "Vuillemin", "Zwahlen"],
    "IT": ["Amato", "Basile", "Caruso", "De Luca", "Ferrari", "Gatti",
           "Innocenti", "Longo", "Mancini", "Negri", "Orlando", "Pagano",
           "Rinaldi", "Sartori", "Trevisan", "Vitale", "Zanetti", "Bellini",
           "Conti", "Donati"],
}

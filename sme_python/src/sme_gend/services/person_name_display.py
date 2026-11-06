"""Presentation only; never change the stored civil identity.

Legacy identity is one field. Follow the dossier's first-word surname
convention for all-uppercase values; preserve an existing uppercase surname
prefix when the given names already have mixed case.
"""


def format_nom_prenoms(value: str | None, *, surname_words: int | None = None) -> str:
    words = (value or '').split()
    if not words:
        return ''
    explicit = surname_words is not None
    surname_words = surname_words if explicit else 1
    if not explicit and words[0].isupper() and not all(word.isupper() for word in words):
        for index, word in enumerate(words[1:], 1):
            if not word.isupper():
                surname_words = index
                break
    elif not explicit:
        # Unambiguous leading surname particles; other compounds need an
        # explicit boundary, never a dictionary of guessed given names.
        particles = {'DE', 'DU', 'DES', 'DA', 'DOS', 'VAN', 'VON', 'LE', 'LA'}
        while surname_words < len(words) and words[surname_words - 1].upper() in particles:
            surname_words += 1
    return ' '.join([*(word.upper() for word in words[:surname_words]),
                     *(word.lower().title() for word in words[surname_words:])])


def _source(person):
    return ' '.join((person.noms or '').split()).casefold()


def format_person_name(person, cfg) -> str:
    entry = cfg.name_formats.get(str(person.satellite_join_key()), {})
    count = entry.get('surname_words')
    if (entry.get('source') == _source(person) and type(count) is int
            and 1 <= count <= len((person.noms or '').split())):
        return format_nom_prenoms(person.noms, surname_words=count)
    return format_nom_prenoms(person.noms)


def set_name_boundary(cfg, person, family_name: str, given_names: str) -> None:
    family, given = ' '.join(family_name.split()), ' '.join(given_names.split())
    if not family or ' '.join(filter(None, (family, given))).casefold() != _source(person):
        raise ValueError("Répartissez les mots existants entre nom et prénoms sans modifier l'identité. Pour corriger l'identité, utilisez la fiche agent.")
    cfg.name_formats[str(person.satellite_join_key())] = {
        'source': _source(person), 'surname_words': len(family.split()),
    }

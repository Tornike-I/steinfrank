r"""Генерирует учебные PDF для демо (вымышленные университеты).

Запуск:  backend\.venv\Scripts\python demo_data\make_samples.py
Файлы сознательно отличаются форматом подписей («Tuition», «Annual fee», «Fees»…) и валютой —
парсеру приходится быть устойчивым, а набор C проверяет умение работать с разными валютами.
"""
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).parent

SETS = {
    "set_a": [
        ("Northvale University", "Boston, USA", [
            "Tuition: $28,500 per year", "World ranking: #42", "Acceptance rate: 38%",
            "Minimum English: IELTS 6.5", "Application deadline: 15 January 2027",
            "Programs: Computer Science, Economics, Biology"]),
        ("Kestrel Institute of Technology", "Pasadena, USA", [
            "Annual fee: USD 41,200", "QS-style rank: 17", "Admission rate: 12.5%",
            "English requirement: IELTS 7.0", "Applications close on 1 December 2026",
            "Programs: Engineering, Physics, Robotics"]),
        ("Lakeshore College", "Chicago, USA", [
            "Tuition & fees: $19,800 / year", "National rank 118", "Acceptance: 61 %",
            "IELTS minimum 6.0", "Deadline: 1 March 2027",
            "Programs: Business, Education, Nursing"]),
    ],
    "set_b": [
        ("Summit State University", "Denver, USA", [
            "Tuition: $15,900 per year", "World ranking: #96", "Acceptance rate: 72%",
            "Minimum English: IELTS 6.0", "Application deadline: 30 April 2027",
            "Programs: Environmental Science, Geology, Law"]),
        ("Harborview Institute", "Seattle, USA", [
            "Annual fee: USD 36,750", "QS-style rank: 29", "Admission rate: 21%",
            "English requirement: IELTS 7.0", "Applications close on 15 December 2026",
            "Programs: Data Science, Maritime Studies, Design"]),
        ("Elmwood University", "Atlanta, USA", [
            "Tuition & fees: $24,300 / year", "National rank 61", "Acceptance: 49 %",
            "IELTS minimum 6.5", "Deadline: 10 February 2027",
            "Programs: Medicine, Public Health, History"]),
    ],
    "set_c": [
        ("Brno Technical Institute", "Brno, Czechia", [
            "Tuition: CZK 85,000 per year", "World ranking: #310", "Acceptance rate: 55%",
            "Minimum English: IELTS 6.0", "Application deadline: 31 March 2027",
            "Programs: Informatics, Mechanical Engineering"]),
        ("Rhein-Main Universitat", "Frankfurt, Germany", [
            "Annual fee: EUR 3,200", "QS-style rank: 88", "Admission rate: 34%",
            "English requirement: IELTS 6.5", "Applications close on 15 January 2027",
            "Programs: Finance, Physics, Linguistics"]),
        ("Thames Valley College", "Reading, UK", [
            "Tuition & fees: \u00a314,500 / year", "National rank 73", "Acceptance: 58 %",
            "IELTS minimum 6.5", "Deadline: 28 January 2027",
            "Programs: Business, Media, Computing"]),
        ("Pacific Coast University", "San Diego, USA", [
            "Tuition: $32,000 per year", "World ranking: #51", "Acceptance rate: 27%",
            "Minimum English: IELTS 7.0", "Application deadline: 5 January 2027",
            "Programs: Oceanography, Computer Science, Film"]),
    ],
}


def write_pdf(path: Path, name: str, place: str, lines: list[str]) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 20)
    c.drawString(60, h - 80, name)
    c.setFont("Helvetica", 11)
    c.drawString(60, h - 100, place + " - International Admissions Fact Sheet")
    y = h - 150
    c.setFont("Helvetica", 13)
    for line in lines:
        c.drawString(70, y, line)
        y -= 26
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(60, 60, "Fictional institution created for the Frankenstein demo. Figures are invented.")
    c.save()


if __name__ == "__main__":
    for folder, unis in SETS.items():
        (ROOT / folder).mkdir(exist_ok=True)
        for name, place, lines in unis:
            fname = name.lower().replace(" ", "_") + ".pdf"
            write_pdf(ROOT / folder / fname, name, place, lines)
    print("PDF samples written to", ROOT)

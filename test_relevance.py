"""python3 -m unittest test_relevance -v"""
import json, os, unittest
import relevance as R

def J(title, inst="Some University", snippet="", field="CS", source="Chronicle of Higher Education Jobs"):
    return {"title": title, "institution": inst, "snippet": snippet, "field": field, "source": source}

DROP = ["College of Arts and Sciences | Adjunct Professor Online, Astrophysics",
        "Adjunct Faculty - COAS - Physics", "Assistant Professor of Mathematics", "Assistant Professor of Chemistry",
        "Part-Time Skills Instructor, Pharmacy Technology Lab", "Adjunct Faculty, Fire Science Technology",
        "Assistant Professor of Mechanical Engineering", "Lecturer, Construction Engineering NTT",
        "Instructor, Health Information Technology - Adjunct (Pool)", "Associate/Full Professor In Social Work, Ai",
        "Assistant Professor - Philosophy of Science / Philosophy of AI", "Tenure-Track Professor in Statistics",
        "Assistant Professor of Biology"]
KEEP = ["Assistant Professor of Computer Science", "Part-time Lecturer - Electrical and Computer Engineering",
        "Assistant Professor - AI for Biology - Electrical Engineering & Computer Sciences",
        "Assistant Professor, Tenure Track - Robotics in Mechanical Engineering",
        "Full-time Electrical Engineering or Engineering Physics Faculty",
        "Tenure-Track Faculty Position in Quantum Computing at the School of Computer Science",
        "Assistant Professor (Tenure-Track) Position in Quantum and Semiconductor Engineering",
        "Assistant Professor- Photonics Quantum Technologies", "Tenure Track Assistant Professor in Computer Science - Bioinformatics",
        "Assistant Professor of Statistics and Data Science", "Assistant Professor of Civil Engineering + AI",
        "Adjunct Instructor - Information Technology (part-time)", "Part Time Electrical Circuits Instructor",
        "Faculty - Electronics Engineering Technology", "Assistant Professor of Mathematics and Computer Science",
        "Part-Time Faculty- Cybersecurity, Networking and Digital Forensics", "Professor in Power Electronics",
        "Assistant Professor in Human-Computer Interaction", "Assistant Professor, Materials Science (Semiconductors)"]

class T(unittest.TestCase):
    def test_drop(self):
        for t in DROP: self.assertFalse(R.classify(J(t, field="Other"))[0], t)
    def test_keep(self):
        for t in KEEP: self.assertTrue(R.classify(J(t))[0], t)
    def test_lowercase_it_is_not_IT(self):
        self.assertFalse(R.classify(J("Adjunct Faculty - Physics", snippet="it is a great place", field="Other"))[0])
    def test_generic_title_uses_snippet(self):
        self.assertTrue(R.classify(J("Assistant Professor", snippet="The Department of Computer Science and Engineering invites", field="Other"))[0])
        self.assertFalse(R.classify(J("Assistant Professor", snippet="School of Government and Policy seeks", field="Other", source="CRA Career Center"))[0])
    @unittest.skipUnless(os.path.exists("jobs.json"), "no jobs.json")
    def test_no_core_eecs_listing_dropped(self):
        import re
        core = re.compile(r"computer science|computer engineering|electrical|\bECE\b|\bEECS\b|software|cyber", re.I)
        for j in json.load(open("jobs.json"))["jobs"]:
            if core.search(j["title"]): self.assertTrue(R.classify(j)[0], j["title"])

if __name__ == "__main__": unittest.main()

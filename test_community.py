"""python3 -m unittest test_community -v  (no network: URL checks are stubbed)"""
import csv, io, json, os, shutil, tempfile, unittest
from datetime import date, timedelta
import community as C

TODAY = date(2026, 10, 2)
HEAD = ["Timestamp", "Job title", "Institution", "Department", "Posting URL", "Location / remote",
        "Application deadline (optional)", "Field / area", "Notes"]

def make_csv(*rows, header=HEAD):
    buf = io.StringIO(); w = csv.writer(buf); w.writerow(header)
    for r in rows: w.writerow(r)
    return buf.getvalue()

def row(title="Assistant Professor of Computer Science", inst="Example State University", dept="Computer Science",
        url="https://jobs.example.edu/postings/123", loc="Portland, OR", deadline="", field="Computer Science", notes="", ts="10/2/2026 9:15:00"):
    return [ts, title, inst, dept, url, loc, deadline, field, notes]

OK = lambda url: (True, "")

class Parsing(unittest.TestCase):
    def test_parse_csv_maps_headers(self):
        (rid, sub), = C.parse_csv(make_csv(row(notes="Remote OK\nline 2")))
        self.assertEqual(sub["title"], "Assistant Professor of Computer Science")
        self.assertEqual(sub["url"], "https://jobs.example.edu/postings/123")
        self.assertEqual(sub["field"], "Computer Science")
        self.assertIn("\n", sub["notes"]); self.assertEqual(len(rid), 16)

    def test_header_variants_and_column_order(self):
        hdr = ["Posting URL (required)", "Timestamp", "JOB TITLE", "Institution name", "Notes"]
        (_, sub), = C.parse_csv(make_csv(["https://x.example.org/j", "t", "Lecturer in EE", "Uni", ""], header=hdr))
        self.assertEqual((sub["title"], sub["institution"], sub["url"]), ("Lecturer in EE", "Uni", "https://x.example.org/j"))

    def test_bad_header_raises(self):
        with self.assertRaises(ValueError): C.parse_csv("Timestamp,Name\n1,2\n")

    def test_bom_blank_rows_and_clamping(self):
        text = "\ufeff" + make_csv(row(title="A" * 500 + " Computer Science"), ["", "", "", "", "", "", "", "", ""])
        rows = C.parse_csv(text)
        self.assertEqual(len(rows), 1); self.assertLessEqual(len(rows[0][1]["title"]), C.LIMITS["title"])

    def test_row_id_stable_and_distinct(self):
        a, b = row(), row(ts="10/2/2026 9:16:00")
        self.assertEqual(C.row_id(a), C.row_id(list(a))); self.assertNotEqual(C.row_id(a), C.row_id(b))

    def test_parse_deadline(self):
        self.assertEqual(C.parse_deadline("2026-12-01"), date(2026, 12, 1))
        self.assertEqual(C.parse_deadline("12/01/2026"), date(2026, 12, 1))
        self.assertEqual(C.parse_deadline("December 1, 2026"), date(2026, 12, 1))
        self.assertIsNone(C.parse_deadline("")); self.assertIsNone(C.parse_deadline("Open until filled"))
        with self.assertRaises(ValueError): C.parse_deadline("sometime in January")

class Validation(unittest.TestCase):
    def ev(self, scraped=(), community=(), checker=OK, **kw):
        (rid, sub), = C.parse_csv(make_csv(row(**kw)))
        return C.evaluate(sub, rid, list(scraped), list(community), TODAY, checker)

    def assertRejected(self, code, **kw):
        with self.assertRaises(C.Rejected) as cm: self.ev(**kw)
        self.assertEqual(cm.exception.code, code, cm.exception.reason)
        return cm.exception

    def test_valid_submission(self):
        job, note = self.ev(deadline="2026-12-01", loc="Remote", notes="fully online")
        self.assertTrue(job["community"]); self.assertEqual(job["tags"], ["community-added"])
        self.assertEqual(job["expires"], "2026-12-01"); self.assertEqual(job["field"], "CS")
        self.assertEqual(job["state"], "Remote"); self.assertEqual(job["modality"], "Remote/Online")
        self.assertEqual(job["source"], C.SOURCE); self.assertEqual(note, "")

    def test_required_fields(self):
        self.assertRejected("invalid", url="")
        self.assertRejected("invalid", title="")

    def test_url_must_be_http(self):
        for u in ("javascript:alert(1)", "ftp://x.edu/a", "file:///etc/passwd", "https://127.0.0.1/x", "https://user:pw@x.edu/a", "not a url"):
            self.assertRejected("invalid", url=u)

    def test_spam_guards(self):
        self.assertRejected("spam", title="Professor of Computer Science casino bonus")
        self.assertRejected("spam", title="Computer Science http://spam.example.com")
        self.assertRejected("spam", inst="--")
        self.assertRejected("spam", notes="CS job " + "!" * 20)

    def test_relevance(self):
        e = self.assertRejected("off-topic", title="Adjunct Professor Online, Astrophysics", dept="", field="Other EECS-related")
        self.assertIn("Astro", e.reason)
        self.assertRejected("off-topic", title="Instructor, Health Information Technology", dept="", field="Information Technology")
        job, _ = self.ev(title="Assistant Professor, Robotics in Mechanical Engineering", dept="Mechanical Engineering", field="Robotics")
        self.assertTrue(job)

    def test_past_deadline(self):
        self.assertRejected("invalid", deadline="2026-01-01")

    def test_bad_deadline_text(self):
        self.assertRejected("invalid", deadline="soonish")

    def test_link_check_results(self):
        self.assertRejected("bad-link", checker=lambda u: (False, "the posting URL returns HTTP 404 (not found)"))
        self.assertRejected("unreachable", checker=lambda u: (False, "the posting URL couldn't be loaded (URLError: timeout)"))
        job, note = self.ev(checker=lambda u: (True, "linkedin.com blocks automated checks"))
        self.assertEqual(job["link_note"], note)

class UrlCheck(unittest.TestCase):
    pub = staticmethod(lambda h: (True, ""))
    def test_status_handling(self):
        self.assertEqual(C.check_url("https://a.example.edu/j", lambda u: (200, u), self.pub), (True, ""))
        self.assertTrue(C.check_url("https://a.example.edu/j", lambda u: (301, u), self.pub)[0])
        ok, note = C.check_url("https://www.linkedin.com/jobs/view/1", lambda u: (999, u), self.pub)
        self.assertTrue(ok); self.assertIn("blocks automated checks", note)
        ok, note = C.check_url("https://jobs.chronicle.com/job/1", lambda u: (403, u), self.pub)
        self.assertTrue(ok)
        self.assertFalse(C.check_url("https://random.example.com/j", lambda u: (403, u), self.pub)[0])  # unknown host: no pass
        self.assertFalse(C.check_url("https://a.example.edu/j", lambda u: (404, u), self.pub)[0])
        self.assertFalse(C.check_url("https://a.example.edu/j", lambda u: (500, u), self.pub)[0])
    def test_private_hosts_blocked(self):
        self.assertFalse(C.check_url("https://intranet.example.edu/j", lambda u: (200, u), lambda h: (False, "private"))[0])
        self.assertFalse(C._host_is_public("localhost")[0])

class Dedup(unittest.TestCase):
    def test_normalize_url(self):
        n = C.normalize_url
        self.assertEqual(n("http://WWW.Example.edu/jobs/1/?utm_source=x&id=5#top"), n("https://example.edu/jobs/1?id=5"))
        self.assertEqual(n("https://jobs.chronicle.com/job/123/x/?TrackID=9"), n("https://jobs.chronicle.com/job/123/x"))
        self.assertNotEqual(n("https://example.edu/jobs/1"), n("https://example.edu/jobs/2"))

    def test_same_job(self):
        a = {"title": "Assistant Professor of Computer Science", "institution": "Example State University", "url": "https://a.edu/1"}
        b = {"title": "Assistant Professor of Computer Science", "institution": "Example State University (Computer Science)", "url": "https://other.org/9"}
        c = {"title": "Lecturer", "institution": "Elsewhere", "url": "http://www.a.edu/1/"}
        d = {"title": "Assistant Professor of Computer Science", "institution": "Different College", "url": "https://d.edu/x"}
        self.assertTrue(C.same_job(a, b)); self.assertTrue(C.same_job(a, c)); self.assertFalse(C.same_job(a, d))
        e = {"title": "Assistant Professor in Computer Science", "institution": "Indiana University East", "url": "https://e.edu/1"}
        f = {"title": "Assistant Professor in Computer Science", "institution": "Indiana University South Bend", "url": "https://f.edu/1"}
        self.assertFalse(C.same_job(e, f))                       # shared prefix is not the same campus

    def test_duplicate_of_scraped_and_community(self):
        scraped = [{"title": "Assistant Professor of Computer Science", "institution": "Example State University",
                    "url": "https://jobs.chronicle.com/job/1", "alt_urls": [], "source": "Chronicle of Higher Education Jobs"}]
        (rid, sub), = C.parse_csv(make_csv(row()))
        with self.assertRaises(C.Rejected) as cm: C.evaluate(sub, rid, scraped, [], TODAY, OK)
        self.assertEqual(cm.exception.code, "duplicate")
        job, _ = C.evaluate(sub, rid, [], [], TODAY, OK)
        (rid2, sub2), = C.parse_csv(make_csv(row(url="https://jobs.example.edu/postings/123?utm_source=li", ts="x")))
        with self.assertRaises(C.Rejected) as cm: C.evaluate(sub2, rid2, [], [job], TODAY, OK)
        self.assertEqual(cm.exception.code, "duplicate")
        expired = dict(job, expires="2026-01-01")      # expired community job doesn't block a resubmission
        self.assertTrue(C.evaluate(sub2, rid2, [], [expired], TODAY, OK)[0])

class Expiry(unittest.TestCase):
    def test_expires_on(self):
        self.assertEqual(C.expires_on(None, TODAY), TODAY + timedelta(days=90))
        self.assertEqual(C.expires_on(date(2026, 11, 1), TODAY), date(2026, 11, 1))
        self.assertEqual(C.expires_on(date(2030, 1, 1), TODAY), TODAY + timedelta(days=90))   # absurd deadline

    def test_merge_expiry_and_scraped_wins(self):
        live = {"title": "Lecturer in Electrical Engineering", "institution": "A Univ", "url": "https://a.edu/1", "posted": "2026-10-01",
                "community": True, "expires": "2026-10-02"}
        gone = dict(live, title="Lecturer in Computer Science", url="https://a.edu/2", expires="2026-10-01")
        dup = dict(live, title="Professor of Computer Engineering", url="https://b.edu/3", expires="2026-12-31")
        scraped = [{"title": "Professor of Computer Engineering", "institution": "A Univ", "url": "https://chronicle.example/9", "posted": "2026-10-02"}]
        merged, active, pruned, shown = C.merge(scraped, [live, gone, dup], TODAY)
        self.assertEqual(pruned, 1); self.assertEqual(shown, 1); self.assertEqual(len(active), 2)
        self.assertEqual([j["url"] for j in merged], ["https://chronicle.example/9", "https://a.edu/1"])
        self.assertNotIn("community", merged[0])                  # scraped listing stays untagged

class IntakeEndToEnd(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.p = lambda n: os.path.join(self.d, n)
        with open(self.p("jobs.json"), "w") as f:
            json.dump({"generated": "x", "sources": {}, "count": 1, "jobs": [
                {"title": "Lecturer in Computer Science", "institution": "Scraped U", "url": "https://s.edu/1", "posted": "2026-09-30"}]}, f)
    def tearDown(self): shutil.rmtree(self.d)
    def run_intake(self, text, **kw):
        return C.intake(text, today=TODAY, url_checker=kw.pop("checker", OK), jobs_path=self.p("jobs.json"),
                        community_path=self.p("c.json"), processed_path=self.p("p.json"), log_path=self.p("rej.log"), **kw)

    def test_full_flow(self):
        text = make_csv(row(),                                                             # added
                        row(title="Adjunct Professor Online, Astrophysics", dept="", ts="2"),  # off-topic
                        row(title="Lecturer in Computer Science", inst="Scraped U", url="https://s.edu/1", ts="3"),  # dup
                        row(title="CS job", url="javascript:x", ts="4"))                   # invalid
        res = self.run_intake(text)
        self.assertEqual(len(res["added"]), 1); self.assertEqual(sorted(r["code"] for r in res["rejected"]), ["duplicate", "invalid", "off-topic"])
        jobs = json.load(open(self.p("jobs.json")))
        self.assertEqual(jobs["count"], 2); self.assertEqual(jobs["community_count"], 1); self.assertEqual(jobs["scraped_count"], 1)
        self.assertTrue(any(j.get("community") and "community-added" in j["tags"] for j in jobs["jobs"]))
        log = open(self.p("rej.log")).read()
        self.assertEqual(log.count("\n"), 3); self.assertNotIn("javascript", log)          # no submitted URLs in public log
        res2 = self.run_intake(text)                                                       # idempotent: nothing new
        self.assertEqual((res2["new"], len(res2["added"])), (0, 0))
        self.assertEqual(json.load(open(self.p("jobs.json")))["count"], 2)
        self.assertIn("Added (1)", C.summary_markdown(res))

    def test_cap_per_run(self):
        text = make_csv(*[row(title=f"Lecturer {i} in Computer Science", url=f"https://e.edu/{i}", ts=str(i)) for i in range(5)])
        res = self.run_intake(text, max_per_run=2)
        self.assertEqual((len(res["added"]), res["deferred"]), (2, 3))
        res = self.run_intake(text, max_per_run=10)
        self.assertEqual((len(res["added"]), res["deferred"]), (3, 0))

    def test_unreachable_retried_then_rejected(self):
        bad = lambda u: (False, "the posting URL couldn't be loaded (URLError: timed out)")
        text = make_csv(row())
        for i in range(C.MAX_RETRIES - 1):
            res = self.run_intake(text, checker=bad); self.assertEqual((res["retrying"], len(res["rejected"])), (1, 0))
        res = self.run_intake(text, checker=bad); self.assertEqual(len(res["rejected"]), 1)
        self.assertEqual(self.run_intake(text)["new"], 0)

    def test_expired_pruned_from_file(self):
        self.run_intake(make_csv(row()))
        res = C.merge_files(self.p("jobs.json"), self.p("c.json"), TODAY + timedelta(days=91))
        self.assertEqual((res["pruned"], res["active"], res["shown"]), (1, 0, 0))
        self.assertEqual(json.load(open(self.p("c.json")))["jobs"], [])

if __name__ == "__main__": unittest.main()

import unittest

from scraper import parse


class ParseResponseTests(unittest.TestCase):
    def test_cart_count_reads_course_icons_cart_span(self) -> None:
        html = """
        <div class="course-info-item">
          <input type="hidden" name="openSchyy" value="2026">
          <input type="hidden" name="openShtmFg" value="U000200002">
          <input type="hidden" name="openDetaShtmFg" value="U000300001">
          <input type="hidden" name="sbjtCd" value="200.106">
          <input type="hidden" name="ltNo" value="002">
          <input type="hidden" name="sbjtSubhCd" value="000">
          <ul class="course-info">
            <li class="txt"><span>교수</span><span>학과</span><span>200.106(002)</span></li>
            <li class="txt">
              <span>수강신청인원/정원(재학생) <em>12/30 (30)</em></span>
              <span>총수강인원 <em>12</em></span>
              <span>학점 <em>3</em></span>
            </li>
          </ul>
          <div class="course-icons">
            <span class="carts"><em title="장바구니"></em>17</span>
          </div>
        </div>
        """

        result = parse.parse_response(html)

        self.assertEqual(result["classes"][0]["cart"], 17)


if __name__ == "__main__":
    unittest.main()

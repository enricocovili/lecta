// «Scarica ▾»: the PDF and the LaTeX source of a published course (and the chapter being read) in one small menu.
// A <details>, so it opens without JavaScript too; Public.astro closes it on an outside click, Escape and a pick.
import { Icon } from "../icons";
import { sizeIt, type PubChapter, type PubCourse } from "./format";

export default function DownloadMenu({
  course,
  chapter,
  primary = false,
  compact = false,
}: {
  course: PubCourse;
  /** The chapter being read: its own PDF joins the menu. */
  chapter?: PubChapter;
  primary?: boolean;
  /** On a phone only the icon shows. */
  compact?: boolean;
}) {
  return (
    <details className={`dropdown pub-dlmenu${compact ? " compact" : ""}`}>
      <summary className={`btn${primary ? " primary" : ""}`} aria-label="Scarica" title="Scarica il PDF o il sorgente LaTeX">
        <Icon name="download" />
        <span className="pub-dl-lbl">Scarica</span>
        <Icon name="chevron-down" className="pub-dl-caret" />
      </summary>
      <div className="menu-pop" role="menu">
        <a href={course.pdf_url} download={`${course.slug}.pdf`} role="menuitem">
          <Icon name="file-text" />
          <span className="t">PDF</span>
          <span className="p">{sizeIt(course.pdf_size)}</span>
        </a>
        {course.source_url && (
          <a href={course.source_url} download={`${course.slug}-latex.zip`} role="menuitem">
            <Icon name="code" />
            <span className="t">Sorgente LaTeX</span>
            <span className="p">ZIP{course.source_size ? ` · ${sizeIt(course.source_size)}` : ""}</span>
          </a>
        )}
        {chapter?.pdf_url && (
          <a href={chapter.pdf_url} download={`${course.slug}-${chapter.slug}.pdf`} role="menuitem">
            <Icon name="file" />
            <span className="t">Solo capitolo {chapter.position}</span>
            <span className="p">PDF · {sizeIt(chapter.pdf_size)}</span>
          </a>
        )}
      </div>
    </details>
  );
}

"""impact_report_service — rapports d'impact agrégés pour bailleurs/accréditations
(Malayka Institution, Phase 5).

Ne lit QUE Structure/StructureMember/Classroom/ClassroomMembership/ClassroomCourse* —
jamais Goal/Plan/Document (cf. structure_access.py, point de vérité unique sur cet
invariant). Toute requête est filtrée par structure_id : aucune agrégation ne doit
jamais croiser deux structures (multi-tenant strict).
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models.structure import (
    Classroom,
    ClassroomCourse,
    ClassroomCourseKind,
    ClassroomCourseRecipient,
    ClassroomMembership,
    MembershipStatus,
    Structure,
    StructureMember,
    StructureMemberRole,
)
from app.services.classroom_course_service import step_completion


def build_impact_report(
    db: Session,
    *,
    structure_id: uuid.UUID,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict:
    """Agrège l'activité de TOUTE la structure (toutes ses Classroom), sur la période
    donnée si fournie. Lecture seule, aucune écriture."""
    structure = db.get(Structure, structure_id)
    if not structure:
        raise NotFoundError("Structure")

    classroom_ids = list(
        db.execute(select(Classroom.id).where(Classroom.structure_id == structure_id)).scalars().all()
    )
    classrooms = list(
        db.execute(select(Classroom).where(Classroom.structure_id == structure_id)).scalars().all()
    )

    def _courses_query(kind: ClassroomCourseKind):
        q = select(ClassroomCourse).where(
            ClassroomCourse.classroom_id.in_(classroom_ids), ClassroomCourse.kind == kind,
        )
        if since:
            q = q.where(ClassroomCourse.created_at >= since)
        if until:
            q = q.where(ClassroomCourse.created_at <= until)
        return q

    courses = list(db.execute(_courses_query(ClassroomCourseKind.course)).scalars().all()) if classroom_ids else []
    evolution_plans = (
        list(db.execute(_courses_query(ClassroomCourseKind.evolution_plan)).scalars().all())
        if classroom_ids else []
    )
    course_ids = [c.id for c in courses]

    recipient_ids = list(
        db.execute(
            select(ClassroomCourseRecipient.id).where(ClassroomCourseRecipient.course_id.in_(course_ids))
        ).scalars().all()
    ) if course_ids else []
    done, total = step_completion(db, recipient_ids)

    students_count = len(set(
        db.execute(
            select(ClassroomMembership.user_id).where(
                ClassroomMembership.classroom_id.in_(classroom_ids),
                ClassroomMembership.status == MembershipStatus.accepted,
            )
        ).scalars().all()
    )) if classroom_ids else 0

    teachers_count = len(
        db.execute(
            select(StructureMember.id).where(
                StructureMember.structure_id == structure_id,
                StructureMember.role == StructureMemberRole.teacher,
            )
        ).scalars().all()
    )

    by_classroom = []
    for classroom in classrooms:
        c_course_ids = [c.id for c in courses if c.classroom_id == classroom.id]
        c_recipient_ids = list(
            db.execute(
                select(ClassroomCourseRecipient.id).where(ClassroomCourseRecipient.course_id.in_(c_course_ids))
            ).scalars().all()
        ) if c_course_ids else []
        c_done, c_total = step_completion(db, c_recipient_ids)
        c_students = db.execute(
            select(ClassroomMembership.id).where(
                ClassroomMembership.classroom_id == classroom.id,
                ClassroomMembership.status == MembershipStatus.accepted,
            )
        ).scalars().all()
        by_classroom.append({
            "classroom_id": classroom.id, "name": classroom.name,
            "students_count": len(c_students), "courses_count": len(c_course_ids),
            "completion_pct": round(c_done / c_total * 100) if c_total else 0,
        })

    subjects = sorted({c.subject for c in courses if c.subject})
    by_subject = []
    for subject in subjects:
        s_course_ids = [c.id for c in courses if c.subject == subject]
        s_recipient_ids = list(
            db.execute(
                select(ClassroomCourseRecipient.id).where(ClassroomCourseRecipient.course_id.in_(s_course_ids))
            ).scalars().all()
        )
        s_done, s_total = step_completion(db, s_recipient_ids)
        by_subject.append({
            "subject": subject, "courses_count": len(s_course_ids),
            "completion_pct": round(s_done / s_total * 100) if s_total else 0,
        })

    return {
        "structure_id": structure.id,
        "structure_name": structure.name,
        "generated_at": datetime.now(timezone.utc),
        "period_since": since,
        "period_until": until,
        "classrooms_count": len(classrooms),
        "teachers_count": teachers_count,
        "students_count": students_count,
        "courses_count": len(courses),
        "evolution_plans_count": len(evolution_plans),
        "completion_pct": round(done / total * 100) if total else 0,
        "by_classroom": by_classroom,
        "by_subject": by_subject,
    }


def export_csv(report: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Rapport d'impact", report["structure_name"]])
    writer.writerow(["Généré le", report["generated_at"].isoformat()])
    if report["period_since"] or report["period_until"]:
        writer.writerow([
            "Période",
            f"{report['period_since'].date() if report['period_since'] else '...'} "
            f"→ {report['period_until'].date() if report['period_until'] else '...'}",
        ])
    writer.writerow([])
    writer.writerow(["Salles", report["classrooms_count"]])
    writer.writerow(["Enseignants", report["teachers_count"]])
    writer.writerow(["Étudiants touchés", report["students_count"]])
    writer.writerow(["Cours envoyés", report["courses_count"]])
    writer.writerow(["Plans personnalisés générés", report["evolution_plans_count"]])
    writer.writerow(["Taux de complétion global (%)", report["completion_pct"]])
    writer.writerow([])
    writer.writerow(["Par Salle"])
    writer.writerow(["Nom", "Étudiants", "Cours envoyés", "Complétion (%)"])
    for row in report["by_classroom"]:
        writer.writerow([row["name"], row["students_count"], row["courses_count"], row["completion_pct"]])
    writer.writerow([])
    writer.writerow(["Par matière"])
    writer.writerow(["Matière", "Cours envoyés", "Complétion (%)"])
    for row in report["by_subject"]:
        writer.writerow([row["subject"], row["courses_count"], row["completion_pct"]])
    return output.getvalue()


def export_pdf(report: dict) -> bytes:
    """Met en page le rapport en PDF via reportlab (même pattern que
    DocumentService.export_pdf)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    pdf = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReportTitle", parent=styles["Heading1"], fontSize=18, spaceAfter=4)
    subtitle_style = ParagraphStyle("ReportSubtitle", parent=styles["Normal"], fontSize=10, textColor=colors.grey, spaceAfter=16)
    h2_style = ParagraphStyle("ReportH2", parent=styles["Heading2"], fontSize=13, spaceBefore=16, spaceAfter=8)

    story = [
        Paragraph(f"Rapport d'impact — {report['structure_name']}", title_style),
        Paragraph(
            f"Généré le {report['generated_at'].strftime('%d/%m/%Y')} via Malayka Institution"
            + (
                f" — période du {report['period_since'].strftime('%d/%m/%Y')} au {report['period_until'].strftime('%d/%m/%Y')}"
                if report["period_since"] and report["period_until"] else ""
            ),
            subtitle_style,
        ),
    ]

    summary_data = [
        ["Salles", str(report["classrooms_count"])],
        ["Enseignants", str(report["teachers_count"])],
        ["Étudiants touchés", str(report["students_count"])],
        ["Cours envoyés", str(report["courses_count"])],
        ["Plans personnalisés générés", str(report["evolution_plans_count"])],
        ["Taux de complétion global", f"{report['completion_pct']}%"],
    ]
    summary_table = Table(summary_data, colWidths=[9 * cm, 6 * cm])
    summary_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
    ]))
    story.append(summary_table)

    if report["by_classroom"]:
        story.append(Paragraph("Par Salle", h2_style))
        rows = [["Nom", "Étudiants", "Cours envoyés", "Complétion"]]
        rows += [
            [c["name"], str(c["students_count"]), str(c["courses_count"]), f"{c['completion_pct']}%"]
            for c in report["by_classroom"]
        ]
        table = Table(rows, colWidths=[7 * cm, 3 * cm, 3 * cm, 2 * cm])
        table.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(table)

    if report["by_subject"]:
        story.append(Paragraph("Par matière", h2_style))
        rows = [["Matière", "Cours envoyés", "Complétion"]]
        rows += [
            [s["subject"], str(s["courses_count"]), f"{s['completion_pct']}%"]
            for s in report["by_subject"]
        ]
        table = Table(rows, colWidths=[8 * cm, 4 * cm, 3 * cm])
        table.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(table)
    else:
        story.append(Spacer(1, 1))

    pdf.build(story)
    return buffer.getvalue()

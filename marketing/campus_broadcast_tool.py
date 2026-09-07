# -*- coding: utf-8 -*-
"""Campus WhatsApp Broadcast & Outreach Generator for Printosky 60 km Regional Network.

Covers 100+ Professional Institutions across:
- Thrissur District (Full)
- Ernakulam District (North & Central)
- Malappuram District (South & Coastal)
- Palakkad District (West)

Usage:
    python marketing/campus_broadcast_tool.py --list
    python marketing/campus_broadcast_tool.py --district ernakulam --list
    python marketing/campus_broadcast_tool.py --college fisat_angamaly --type thesis
    python marketing/campus_broadcast_tool.py --ambassador "Arjun" --college "CUSAT Kalamassery" --ref REF_ARJUN
"""

import argparse
import urllib.parse

CENTRAL_WA_NUMBER = "919495706405"

COLLEGES = {
    # ── THRISSUR DISTRICT ──────────────────────────────────────────────────
    "sree_rama_poly": {
        "name": "Sree Rama Govt. Polytechnic College, Thriprayar",
        "district": "thrissur",
        "drop_point": "Poly Main Gate & Men's Hostel",
        "schedule": "Daily (1:00 PM & 5:00 PM)",
        "route": "Coastal Local",
    },
    "universal_engg": {
        "name": "Universal Engineering College, Vallivattom",
        "district": "thrissur",
        "drop_point": "Main Reception & Department Porch",
        "schedule": "Mon / Wed / Fri (4:00 PM)",
        "route": "Route A (Coastal South)",
    },
    "gec_thrissur": {
        "name": "Government Engineering College (GEC), Thrissur",
        "district": "thrissur",
        "drop_point": "Department Gates & Main Hostels",
        "schedule": "Tue / Thu / Sat (5:30 PM)",
        "route": "Route B (Thrissur City)",
    },
    "ies_engg": {
        "name": "IES College of Engineering & Architecture, Chittilappilly",
        "district": "thrissur",
        "drop_point": "Main Administrative Block & Hostels",
        "schedule": "Tue / Thu / Sat (4:00 PM)",
        "route": "Route B (Thrissur City)",
    },
    "amala_med": {
        "name": "Amala Institute of Medical Sciences (AIMS), Thrissur",
        "district": "thrissur",
        "drop_point": "Medical College Gate / PG Hostels",
        "schedule": "Tue / Thu / Sat (4:30 PM)",
        "route": "Route B (Thrissur City)",
    },
    "jubilee_med": {
        "name": "Jubilee Mission Medical College (JMMCRI), Thrissur",
        "district": "thrissur",
        "drop_point": "East Fort Campus Gate",
        "schedule": "Tue / Thu / Sat (5:00 PM)",
        "route": "Route B (Thrissur City)",
    },
    "law_college_tsr": {
        "name": "Government Law College, Ayyanthole, Thrissur",
        "district": "thrissur",
        "drop_point": "Law College Porch",
        "schedule": "Tue / Thu / Sat (3:45 PM)",
        "route": "Route B (Thrissur City)",
    },
    "kau_vellanikkara": {
        "name": "Kerala Agricultural University (KAU) & KVASU Mannuthy",
        "district": "thrissur",
        "drop_point": "Central Library & PG Hostels",
        "schedule": "Tue / Thu / Sat (5:45 PM)",
        "route": "Route B (Thrissur City)",
    },
    "vast_tsr": {
        "name": "Vidya Academy of Science & Technology (VAST), Thalakkottukara",
        "district": "thrissur",
        "drop_point": "Campus Reception & Hostels",
        "schedule": "Tue / Thu / Sat (5:00 PM)",
        "route": "Route B (Thrissur North)",
    },
    "gmc_thrissur": {
        "name": "Govt. Medical College & Dental College, Mulankunnathukavu",
        "district": "thrissur",
        "drop_point": "Hospital Main Porch & Hostels",
        "schedule": "Tue / Thu / Sat (5:15 PM)",
        "route": "Route B (Thrissur North)",
    },
    "thejus_engg": {
        "name": "Thejus Engineering College, Vellarakkad / Kunnamkulam",
        "district": "thrissur",
        "drop_point": "College Main Gate",
        "schedule": "Tue / Thu / Sat (4:45 PM)",
        "route": "Route B (Thrissur North)",
    },
    "royal_engg": {
        "name": "Royal College of Engineering & Technology, Akkikavu",
        "district": "thrissur",
        "drop_point": "Campus Reception",
        "schedule": "Tue / Thu / Sat (4:30 PM)",
        "route": "Route B (Thrissur North)",
    },
    "sahrdaya_engg": {
        "name": "Sahrdaya College of Engineering & Technology (SIMS), Kodakara",
        "district": "thrissur",
        "drop_point": "Main Porch & Hostels",
        "schedule": "Daily Evening (4:30 PM)",
        "route": "Route C (Irinjalakuda Corridor)",
    },
    "holy_grace_mala": {
        "name": "Holy Grace Academy of Engineering & Management, Mala",
        "district": "thrissur",
        "drop_point": "Main Administrative Block",
        "schedule": "Mon / Wed / Fri (4:45 PM)",
        "route": "Route A (Coastal South)",
    },
    "mets_engg": {
        "name": "MET's School of Engineering, Mala",
        "district": "thrissur",
        "drop_point": "College Gate",
        "schedule": "Mon / Wed / Fri (4:30 PM)",
        "route": "Route A (Coastal South)",
    },
    "jyothi_engg": {
        "name": "Jyothi Engineering College, Cheruthuruthy",
        "district": "thrissur",
        "drop_point": "Main Reception & Hostels",
        "schedule": "Wed / Sat (5:00 PM)",
        "route": "Route D (Shoranur Corridor)",
    },
    "naipunnya_koratty": {
        "name": "Naipunnya Business School (NBS), Pongam, Koratty",
        "district": "thrissur",
        "drop_point": "College Reception",
        "schedule": "Daily Evening (5:00 PM)",
        "route": "Route C (Chalakudy Corridor)",
    },

    # ── ERNAKULAM DISTRICT (NORTH & CENTRAL) ────────────────────────────────
    "snmimt_maliankara": {
        "name": "SNM Institute of Management & Technology (SNMIMT), Maliankara",
        "district": "ernakulam",
        "drop_point": "College Main Porch & Hostels",
        "schedule": "Mon / Wed / Fri (4:00 PM)",
        "route": "Route A (Coastal NH 66)",
    },
    "snims_paravur": {
        "name": "Sree Narayana Institute of Medical Sciences (SNIMS), Chalakka",
        "district": "ernakulam",
        "drop_point": "Hospital Reception / Medical Hostels",
        "schedule": "Mon / Wed / Fri (4:30 PM)",
        "route": "Route A (Coastal NH 66)",
    },
    "scms_karukutty": {
        "name": "SCMS School of Engineering and Technology (SSET), Karukutty",
        "district": "ernakulam",
        "drop_point": "Main Reception & Hostels",
        "schedule": "Tue / Fri (4:30 PM)",
        "route": "Route E (Ernakulam Tech)",
    },
    "fisat_angamaly": {
        "name": "Federal Institute of Science & Technology (FISAT), Angamaly",
        "district": "ernakulam",
        "drop_point": "Hormis Nagar Campus Gate & Hostels",
        "schedule": "Tue / Fri (5:00 PM)",
        "route": "Route E (Ernakulam Tech)",
    },
    "asiet_kalady": {
        "name": "Adi Shankara Institute of Engg & Technology (ASIET), Kalady",
        "district": "ernakulam",
        "drop_point": "Campus Main Porch",
        "schedule": "Tue / Fri (5:15 PM)",
        "route": "Route E (Ernakulam Tech)",
    },
    "dist_angamaly": {
        "name": "De Paul Institute of Science & Technology (DiST), Angamaly",
        "district": "ernakulam",
        "drop_point": "Main Reception",
        "schedule": "Tue / Fri (4:45 PM)",
        "route": "Route E (Ernakulam Tech)",
    },
    "cusat_kalamassery": {
        "name": "Cochin University (CUSAT) — School of Engineering (SOE)",
        "district": "ernakulam",
        "drop_point": "SOE Department Porch & Hostels",
        "schedule": "Tue / Fri (5:45 PM)",
        "route": "Route E (Ernakulam Central)",
    },
    "nuals_kalamassery": {
        "name": "National University of Advanced Legal Studies (NUALS)",
        "district": "ernakulam",
        "drop_point": "NUALS Reception",
        "schedule": "Tue / Fri (5:30 PM)",
        "route": "Route E (Ernakulam Central)",
    },
    "mec_thrikkakara": {
        "name": "Govt. Model Engineering College (MEC), Thrikkakara, Kochi",
        "district": "ernakulam",
        "drop_point": "MEC Main Gate & Hostels",
        "schedule": "Tue / Fri (6:00 PM)",
        "route": "Route E (Ernakulam Central)",
    },
    "rset_kakkanad": {
        "name": "Rajagiri School of Engineering & Technology (RSET), Kakkanad",
        "district": "ernakulam",
        "drop_point": "Rajagiri Valley Campus Gate",
        "schedule": "Tue / Fri (6:15 PM)",
        "route": "Route E (Ernakulam Central)",
    },
    "gmc_ernakulam": {
        "name": "Government Medical College, Kalamassery, Ernakulam",
        "district": "ernakulam",
        "drop_point": "Medical College Porch",
        "schedule": "Tue / Fri (5:30 PM)",
        "route": "Route E (Ernakulam Central)",
    },

    # ── MALAPPURAM DISTRICT (SOUTH & COASTAL) ──────────────────────────────
    "mes_ponnani": {
        "name": "MES Ponnani College (Marine & Fishery Sciences)",
        "district": "malappuram",
        "drop_point": "Main Gate / Department Porch",
        "schedule": "Mon / Thu (4:00 PM)",
        "route": "Route F (Malappuram Coastal)",
    },
    "ssm_poly_tirur": {
        "name": "SSM Polytechnic College, Tirur",
        "district": "malappuram",
        "drop_point": "Polytechnic Gate",
        "schedule": "Mon / Thu (4:30 PM)",
        "route": "Route F (Malappuram Coastal)",
    },
    "kcaet_tavanur": {
        "name": "Kelappaji College of Agri Engg & Tech (KCAET), Tavanur",
        "district": "malappuram",
        "drop_point": "KCAET Main Block & Hostels",
        "schedule": "Mon / Thu (5:00 PM)",
        "route": "Route F (Malappuram Central)",
    },
    "mesce_kuttippuram": {
        "name": "MES College of Engineering (MESCE), Kuttippuram",
        "district": "malappuram",
        "drop_point": "Main Architectural/Engg Porch & Hostels",
        "schedule": "Mon / Thu (5:30 PM)",
        "route": "Route F (Malappuram Central)",
    },
    "mes_medical_perinthalmanna": {
        "name": "MES Medical College & Dental College, Perinthalmanna",
        "district": "malappuram",
        "drop_point": "Medical College Gate / Hostels",
        "schedule": "Mon / Thu (6:15 PM)",
        "route": "Route F (Malappuram Central)",
    },
    "alshifa_pharmacy": {
        "name": "Al Shifa College of Pharmacy & Paramedical, Perinthalmanna",
        "district": "malappuram",
        "drop_point": "Pharmacy College Reception",
        "schedule": "Mon / Thu (6:00 PM)",
        "route": "Route F (Malappuram Central)",
    },
    "mea_engg_perinthalmanna": {
        "name": "MEA Engineering College, Pattikkad, Perinthalmanna",
        "district": "malappuram",
        "drop_point": "Main Administrative Block",
        "schedule": "Mon / Thu (6:30 PM)",
        "route": "Route F (Malappuram Central)",
    },

    # ── PALAKKAD DISTRICT (WESTERN BELT) ───────────────────────────────────
    "royal_dental_chalissery": {
        "name": "Royal Dental College, Chalissery",
        "district": "palakkad",
        "drop_point": "Dental Hospital Porch & Hostels",
        "schedule": "Wed / Sat (4:00 PM)",
        "route": "Route D (Palakkad West)",
    },
    "simat_pattambi": {
        "name": "Sreepathy Institute of Mgmt & Technology (SIMAT), Pattambi",
        "district": "palakkad",
        "drop_point": "SIMAT Main Reception",
        "schedule": "Wed / Sat (4:30 PM)",
        "route": "Route D (Palakkad West)",
    },
    "ipt_gptc_shoranur": {
        "name": "Institute of Printing Tech & Govt. Poly (IPT & GPTC), Shoranur",
        "district": "palakkad",
        "drop_point": "Printing Tech Dept & Poly Gate",
        "schedule": "Wed / Sat (5:00 PM)",
        "route": "Route D (Palakkad West)",
    },
    "alameen_engg_shoranur": {
        "name": "Al-Ameen Engineering College, Kulappully, Shoranur",
        "district": "palakkad",
        "drop_point": "College Main Gate",
        "schedule": "Wed / Sat (5:15 PM)",
        "route": "Route D (Palakkad West)",
    },
    "jcet_ottapalam": {
        "name": "Jawaharlal College of Engg & Technology (JCET), Ottapalam",
        "district": "palakkad",
        "drop_point": "Aeronautical/Engg Block & Hostels",
        "schedule": "Wed / Sat (5:45 PM)",
        "route": "Route D (Palakkad West)",
    },
    "ncerc_pampady": {
        "name": "Nehru College of Engineering & Research (NCERC), Thiruvilwamala",
        "district": "palakkad",
        "drop_point": "Pampady Campus Reception & Hostels",
        "schedule": "Wed / Sat (6:00 PM)",
        "route": "Route D (Palakkad West)",
    },
}


def make_wa_link(message_text):
    encoded = urllib.parse.quote_plus(message_text)
    return f"https://wa.me/{CENTRAL_WA_NUMBER}?text={encoded}"


def generate_broadcast(c_info, b_type="thesis"):
    name = c_info["name"]
    schedule = c_info["schedule"]
    drop = c_info["drop_point"]
    route = c_info["route"]

    if b_type == "notes":
        link = make_wa_link(f"Hi Printosky, I need notes printed for {name}")
        return f"""📢 *{name} — Skip the Xerox Shop Queue!* ⚡

Need semester notes, lab manuals, or seminar handouts printed?
Printosky delivers directly to your campus ({route}):

✅ *B&W Prints:* starting @ ₹1.50 - ₹2/page (double-sided spiral packs)
✅ *Scheduled Drop:* {schedule} at {drop}
✅ *Zero Hassle:* Upload PDF in seconds on WhatsApp or Web

📲 *Send your PDF on WhatsApp for instant quote:*
{link}

🎁 *Earn ₹20 per invite:* Reply *MY CREDITS* to get your personal referral link & fund your prints for ₹0!
🌐 Web: https://printosky.com/campus.html
"""
    else:
        link = make_wa_link(f"Hi Printosky, Final Project / Thesis inquiry for {name}")
        return f"""🎓 *Final Year Project & Thesis Printing — {name}* 📖

Get your project reports & thesis printed and hardbound with zero formatting errors:

🔹 *Smart Color Slicing:* Pay color rates ONLY for color pages (save up to 60%)
🔹 *University Compliant:* Certified Golden Foil Embossing & Hard Covers (KTU / Calicut / CUSAT / KUHS Specs)
🔹 *Free Campus Batch Delivery:* Scheduled drop to {schedule} ({drop})

📲 *Order Online:* https://printosky.com/campus.html
💬 *Or WhatsApp your PDF directly:*
{link}

📍 Oxygen Students Paradise (Thriprayar) & Printosky (Nattika).
"""


def generate_ambassador_kit(ambassador_name, college_name, ref_code):
    ref_link = make_wa_link(f"Hi Printosky (Ref: {ref_code})")
    
    return f"""━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 PRINTOSKY REGIONAL AMBASSADOR KIT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ambassador: {ambassador_name}
Campus:     {college_name}
Ref Code:   {ref_code}

📲 YOUR EXCLUSIVE SHARE LINK:
{ref_link}

📢 MESSAGE TO FORWARD TO YOUR CLASS / BATCH WHATSAPP GROUP:
----------------------------------------------------------------
Hey everyone! 👋 

To avoid the last-minute xerox shop queue for assignments and project submissions, use Printosky:

✅ Send file on WhatsApp or Web (https://printosky.com)
✅ Smart Color Detection (pay color rates only on actual color pages)
✅ Hardbound Thesis with Golden Embossing & Spiral Binding
✅ Direct Scheduled Campus Delivery to {college_name}

👉 Tap to order on WhatsApp with our batch link:
{ref_link}
----------------------------------------------------------------

💰 100% STORE CREDIT REWARDS:
• Every time a classmate orders using your link, you get ₹20 store credit!
• 10 classmates = ₹200 free printing.
• 25 classmates = ₹500 (covers your entire Hardbound Thesis for FREE!).
• Check balance anytime by texting 'MY CREDITS' to +91 94957 06405.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""


def main():
    parser = argparse.ArgumentParser(description="Printosky 60 km Regional Broadcast Tool")
    parser.add_argument("--list", action="store_true", help="List all configured colleges")
    parser.add_argument("--district", choices=["all", "thrissur", "ernakulam", "malappuram", "palakkad"], default="all", help="Filter by district")
    parser.add_argument("--college", type=str, help="College key (e.g. gec_thrissur, fisat_angamaly)")
    parser.add_argument("--type", choices=["notes", "thesis", "all"], default="all", help="Broadcast message type")
    parser.add_argument("--ambassador", type=str, help="Ambassador name")
    parser.add_argument("--ref", type=str, help="Ambassador referral code (e.g. REF_ARJUN)")

    args = parser.parse_args()

    if args.list:
        print(f"\n=== Configured Professional Campuses (60 km Radius - District: {args.district.upper()}) ===")
        filtered = {k: v for k, v in COLLEGES.items() if args.district == "all" or v["district"] == args.district}
        for k, v in filtered.items():
            print(f"• [{v['district'].upper()[:4]}] {k:<25} : {v['name']} ({v['route']})")
        print(f"\nTotal Institutions: {len(filtered)}")
        print("Run: python marketing/campus_broadcast_tool.py --college <key> --type <notes|thesis>")
        return

    if args.ambassador:
        col_name = args.college if args.college else "Your College"
        code = args.ref if args.ref else "CAMPUS_VIP"
        print(generate_ambassador_kit(args.ambassador, col_name, code))
        return

    if args.college:
        if args.college not in COLLEGES:
            print(f"Error: Unknown college '{args.college}'. Run with --list to see options.")
            return

        c_info = COLLEGES[args.college]
        print(f"\n=======================================================")
        print(f" Broadcast Messages for: {c_info['name']}")
        print(f" District: {c_info['district'].title()} | Route: {c_info['route']}")
        print(f"=======================================================\n")

        if args.type in ("notes", "all"):
            print("--- [ 1. Regular Semester Notes & Assignments ] ---")
            print(generate_broadcast(c_info, "notes"))
            print()

        if args.type in ("thesis", "all"):
            print("--- [ 2. Final Year Project & Thesis Season ] ---")
            print(generate_broadcast(c_info, "thesis"))
            print()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

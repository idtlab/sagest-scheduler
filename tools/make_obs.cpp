// Samples a euler-miniapp snapshot at points and writes the values as a libMLEF observation
// file (the schema in external/libMLEF/ObservationReader.hpp).
//
// Usage: make_obs TRUTH.cgns OUT.cgns < spec
// Each spec line is "x y z variable_id variance"; z is ignored in 2D. Variable ids follow
// libMLEF: 0 Density, 1 MomentumX, 2 MomentumY, 3 MomentumZ, 4 EnergyTotalDensity.
// Each point takes the value of the cell containing it, with cells half-open like libMLEF's
// lookup, so a point on a cell face reads the cell above it.
#include <cgnslib.h>

#include <cmath>
#include <cstdio>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void check(int status, const std::string& what)
{
    if (status != CG_OK) throw std::runtime_error(what + ": " + cg_get_error());
}

int nodes_per(CGNS_ENUMT(ElementType_t) type)
{
    switch (type) {
    case CGNS_ENUMV(QUAD_4): return 4;
    case CGNS_ENUMV(HEXA_8): return 8;
    default: return 0;
    }
}

const char* const FIELDS[] = {"Density", "MomentumX", "MomentumY", "MomentumZ",
                              "EnergyTotalDensity"};

struct Obs { double x[3]; int id; double variance; double value; };

}  // namespace

int main(int argc, char** argv)
{
    if (argc != 3) {
        std::fprintf(stderr, "usage: make_obs TRUTH.cgns OUT.cgns < spec\n");
        return 2;
    }
    try {
        std::vector<Obs> obs;
        Obs o{};
        while (std::cin >> o.x[0] >> o.x[1] >> o.x[2] >> o.id >> o.variance) {
            if (o.id < 0 || o.id > 4) throw std::runtime_error("variable id must be 0..4");
            obs.push_back(o);
        }
        if (!std::cin.eof()) throw std::runtime_error("bad spec line");
        if (obs.empty()) throw std::runtime_error("no observations in spec");

        int fn = 0, cell_dim = 0, dim = 0, nsteps = 0;
        char name[33];
        check(cg_open(argv[1], CG_MODE_READ, &fn), std::string("open ") + argv[1]);
        check(cg_base_read(fn, 1, name, &cell_dim, &dim), "base");
        int iteration = 0;
        double time = 0;
        check(cg_biter_read(fn, 1, name, &nsteps), "BaseIterativeData");
        check(cg_goto(fn, 1, "BaseIterativeData_t", 1, "end"), "goto BaseIterativeData");
        int narrays = 0;
        check(cg_narrays(&narrays), "arrays");
        for (int a = 1; a <= narrays; ++a) {
            CGNS_ENUMT(DataType_t) type;
            int ndims = 0;
            cgsize_t dims[12];
            check(cg_array_info(a, name, &type, &ndims, dims), "array info");
            std::vector<double> values(dims[0]);
            check(cg_array_read_as(a, CGNS_ENUMV(RealDouble), values.data()), name);
            if (std::string(name) == "TimeValues") time = values.back();
            if (std::string(name) == "IterationValues") iteration = int(values.back());
        }

        cgsize_t size[9];
        check(cg_zone_read(fn, 1, 1, name, size), "zone");
        const cgsize_t nvert = size[0], ncell = size[1], one = 1;
        std::vector<std::vector<double>> coord(dim, std::vector<double>(nvert));
        for (int d = 0; d < dim; ++d) {
            char axis[] = "CoordinateX";
            axis[10] = char('X' + d);
            check(cg_coord_read(fn, 1, 1, axis, CGNS_ENUMV(RealDouble), &one, &nvert,
                                coord[d].data()), axis);
        }

        // Each cell's bounding box, indexed as the solution arrays are.
        std::vector<double> lo(ncell * dim, std::numeric_limits<double>::max());
        std::vector<double> hi(ncell * dim, std::numeric_limits<double>::lowest());
        int nsections = 0;
        check(cg_nsections(fn, 1, 1, &nsections), "sections");
        for (int s = 1; s <= nsections; ++s) {
            CGNS_ENUMT(ElementType_t) type;
            cgsize_t start = 0, end = 0;
            int nbndry = 0, parent = 0;
            check(cg_section_read(fn, 1, 1, s, name, &type, &start, &end, &nbndry, &parent),
                  "section");
            const int per = nodes_per(type);
            if (per == 0 || end > ncell) continue;
            std::vector<cgsize_t> conn((end - start + 1) * per);
            check(cg_elements_read(fn, 1, 1, s, conn.data(), nullptr), "elements");
            for (cgsize_t e = 0; e <= end - start; ++e) {
                const cgsize_t cell = start - 1 + e;
                for (int n = 0; n < per; ++n) {
                    for (int d = 0; d < dim; ++d) {
                        const double v = coord[d][conn[e * per + n] - 1];
                        lo[cell * dim + d] = std::min(lo[cell * dim + d], v);
                        hi[cell * dim + d] = std::max(hi[cell * dim + d], v);
                    }
                }
            }
        }

        int nsols = 0, sol = 0;
        check(cg_nsols(fn, 1, 1, &nsols), "solutions");
        for (int s = 1; s <= nsols && !sol; ++s) {
            CGNS_ENUMT(GridLocation_t) where;
            check(cg_sol_info(fn, 1, 1, s, name, &where), "solution info");
            if (where == CGNS_ENUMV(CellCenter)) sol = s;
        }
        if (!sol) throw std::runtime_error("no cell-centered solution");
        std::vector<std::vector<double>> fields(5);
        for (Obs& p : obs) {
            if (p.id == 3 && dim < 3) throw std::runtime_error("MomentumZ needs a 3D snapshot");
            std::vector<double>& field = fields[p.id];
            if (field.empty()) {
                field.resize(ncell);
                check(cg_field_read(fn, 1, 1, sol, FIELDS[p.id], CGNS_ENUMV(RealDouble), &one,
                                    &ncell, field.data()), FIELDS[p.id]);
            }
            cgsize_t found = -1;
            for (cgsize_t c = 0; c < ncell && found < 0; ++c) {
                bool inside = true;
                for (int d = 0; d < dim; ++d) {
                    inside = inside && lo[c * dim + d] <= p.x[d] && p.x[d] < hi[c * dim + d];
                }
                if (inside) found = c;
            }
            if (found < 0) {
                throw std::runtime_error("point (" + std::to_string(p.x[0]) + ", "
                                         + std::to_string(p.x[1]) + ") is outside the grid");
            }
            p.value = field[found];
        }
        check(cg_close(fn), "close");

        const cgsize_t nobs = cgsize_t(obs.size()), nstep = 1;
        int out = 0, base = 0, zone = 0, index = 0;
        check(cg_open(argv[2], CG_MODE_WRITE, &out), std::string("open ") + argv[2]);
        check(cg_base_write(out, "Base", dim, dim, &base), "write base");
        check(cg_biter_write(out, base, "BaseIterativeData", 1), "write BaseIterativeData");
        check(cg_goto(out, base, "BaseIterativeData_t", 1, "end"), "goto BaseIterativeData");
        check(cg_array_write("TimeValues", CGNS_ENUMV(RealDouble), 1, &nstep, &time), "time");
        check(cg_array_write("IterationValues", CGNS_ENUMV(Integer), 1, &nstep, &iteration),
              "iteration");
        cgsize_t zone_size[3] = {nobs, 0, 0};
        check(cg_zone_write(out, base, "Observations", zone_size, CGNS_ENUMV(Unstructured),
                            &zone), "write zone");
        std::vector<double> column(obs.size());
        for (int d = 0; d < dim; ++d) {
            char axis[] = "CoordinateX";
            axis[10] = char('X' + d);
            for (std::size_t i = 0; i < obs.size(); ++i) column[i] = obs[i].x[d];
            check(cg_coord_write(out, base, zone, CGNS_ENUMV(RealDouble), axis, column.data(),
                                 &index), axis);
        }
        check(cg_sol_write(out, base, zone, "ObsData", CGNS_ENUMV(Vertex), &sol), "write ObsData");
        for (std::size_t i = 0; i < obs.size(); ++i) column[i] = obs[i].value;
        check(cg_field_write(out, base, zone, sol, CGNS_ENUMV(RealDouble), "Value",
                             column.data(), &index), "Value");
        for (std::size_t i = 0; i < obs.size(); ++i) column[i] = obs[i].variance;
        check(cg_field_write(out, base, zone, sol, CGNS_ENUMV(RealDouble), "Variance",
                             column.data(), &index), "Variance");
        std::vector<int> ids(obs.size());
        for (std::size_t i = 0; i < obs.size(); ++i) ids[i] = obs[i].id;
        check(cg_field_write(out, base, zone, sol, CGNS_ENUMV(Integer), "VariableID",
                             ids.data(), &index), "VariableID");
        check(cg_close(out), "close output");

        std::printf("make_obs: %zu observations at step %d (t = %g) -> %s\n", obs.size(),
                    iteration, time, argv[2]);
        for (const Obs& p : obs) {
            std::printf("  (%g, %g, %g) %s = %.10g\n", p.x[0], p.x[1], p.x[2], FIELDS[p.id],
                        p.value);
        }
    } catch (const std::exception& e) {
        std::fprintf(stderr, "make_obs: %s\n", e.what());
        return 1;
    }
    return 0;
}

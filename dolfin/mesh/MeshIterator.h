
#ifndef __MESH_ITERATOR_H
#define __MESH_ITERATOR_H

#include <memory>
#include <boost/iterator/iterator_facade.hpp>

#include "Mesh.h"
#include "MeshEntity.h"

namespace dolfin
{
  // Forward declaration
  template <typename T> class entities;

  template<class T>
  class MeshIterator : public boost::iterator_facade<MeshIterator<T>, T, boost::forward_traversal_tag>
  {
  public:

    /// Default constructor
    MeshIterator() : _pos(0), _index(nullptr)
    {}

    /// Copy constructor
    MeshIterator(const MeshIterator& it) : _entity(std::make_unique<T>(it._entity->mesh(), 0)),
      _pos(it._pos), _index(it._index)
    {
      _entity->_local_index = (_index ? _index[_pos] : _pos);
    }

    // Constructor with Mesh
    MeshIterator(const Mesh& mesh, std::size_t pos=0) : _pos(pos), _index(nullptr)
    {
      // Check if mesh is empty
      if (mesh.num_vertices() == 0)
        return;

      // Initialize mesh entity
      _entity = std::make_unique<T>(mesh, 0);
      _entity->_local_index = _pos;
    }

    // Constructor with MeshEntity
    MeshIterator(const MeshEntity& e, std::size_t pos=0) : _entity(std::make_unique<T>(e.mesh(), 0)), _pos(pos)
    {
      // Get connectivity
      const MeshConnectivity& c = e.mesh().topology()(e.dim(), _entity->dim());

      // Compute connectivity if empty
      if (c.empty())
        e.mesh().init(e.dim(), _entity->dim());

      // Set _index to point at connectivity for that entity
      _index = c(e.index());
      _entity->_local_index = _index[_pos];
    }

  private:

    friend class boost::iterator_core_access;

    void increment()
    {
      ++_pos;
      _entity->_local_index = (_index ? _index[_pos] : _pos);
    }

    bool equal(MeshIterator const& other) const
    {
      return (_pos == other._pos and _index == other._index);
    }

    T& dereference() const
    {
      return *_entity;
    }

    // MeshEntity
    std::unique_ptr<T> _entity;

    // Current position
    std::size_t _pos;

    // Mapping from pos to index (if any)
    const unsigned int* _index;

    template <typename T> friend class entities;
  };

  // Class defining begin() and end() methods for a given entity
  template<class T>
  class entities
  {
  public:
    entities(const Mesh& mesh) : _mesh(&mesh)
    {}

    entities(const MeshEntity& e) : _mesh(&e.mesh())
    {}

    const MeshIterator<T> begin() const
    { return MeshIterator<T>(*_mesh); }

    const MeshIterator<T> end() const
    {
      MeshIterator<T> it(*_mesh);
      std::size_t dim = it._entity->dim();
      std::size_t end_pos = _mesh->topology().ghost_offset(dim);
      it._pos = end_pos;
      it._entity->_local_index = (_index ? _index[_pos] : _pos);
      return it;
    }

  private:
    const Mesh *_mesh;
  };

  typedef entities<Cell> cells;
  typedef entities<Vertex> vertices;

}

#endif
